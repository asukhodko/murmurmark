use std::{env, error::Error, path::Path};

use webrtc_audio_processing::{
    Config, Processor,
    config::{EchoCanceller, HighPassFilter},
};

struct MonoAudio {
    samples: Vec<f32>,
    sample_rate: u32,
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 4 || args.len() > 5 {
        eprintln!("usage: {} mic.wav remote.wav out.wav [delay_ms]", args[0]);
        std::process::exit(2);
    }

    let mic = read_wav(&args[1])?;
    let remote = read_wav(&args[2])?;
    if mic.sample_rate != remote.sample_rate {
        return Err("mic and remote sample rates differ".into());
    }

    let delay_ms = args
        .get(4)
        .and_then(|value| value.parse::<u16>().ok())
        .filter(|value| *value > 0);
    let cleaned = process_webrtc_apm(&mic, &remote, delay_ms)?;
    write_wav(&args[3], mic.sample_rate, &cleaned)?;
    Ok(())
}

fn process_webrtc_apm(
    mic: &MonoAudio,
    remote: &MonoAudio,
    delay_ms: Option<u16>,
) -> Result<Vec<f32>, Box<dyn Error>> {
    let processor = Processor::new(mic.sample_rate)?;
    processor.set_config(Config {
        echo_canceller: Some(EchoCanceller::Full {
            stream_delay_ms: delay_ms,
        }),
        high_pass_filter: Some(HighPassFilter::default()),
        ..Default::default()
    });

    let frame_size = processor.num_samples_per_frame();
    let mut output = vec![0.0f32; mic.samples.len()];

    let mut position = 0usize;
    while position < mic.samples.len() {
        let count = (mic.samples.len() - position).min(frame_size);
        let mut render_frame = vec![vec![0.0f32; frame_size]];
        let mut capture_frame = vec![vec![0.0f32; frame_size]];

        for index in 0..count {
            capture_frame[0][index] = mic.samples[position + index].clamp(-1.0, 1.0);
            if position + index < remote.samples.len() {
                render_frame[0][index] = remote.samples[position + index].clamp(-1.0, 1.0);
            }
        }

        processor.process_render_frame(&mut render_frame)?;
        processor.process_capture_frame(&mut capture_frame)?;
        output[position..(count + position)].copy_from_slice(&capture_frame[0][..count]);
        position += count;
    }

    Ok(output)
}

fn read_wav(path: impl AsRef<Path>) -> Result<MonoAudio, Box<dyn Error>> {
    let mut reader = hound::WavReader::open(path)?;
    let spec = reader.spec();
    if spec.channels != 1 {
        return Err("expected mono WAV".into());
    }

    let samples = match spec.sample_format {
        hound::SampleFormat::Float => reader.samples::<f32>().collect::<Result<Vec<_>, _>>()?,
        hound::SampleFormat::Int => {
            if spec.bits_per_sample <= 16 {
                let scale = (1i64 << (spec.bits_per_sample - 1)) as f32;
                reader
                    .samples::<i16>()
                    .map(|sample| sample.map(|value| f32::from(value) / scale))
                    .collect::<Result<Vec<_>, _>>()?
            } else {
                let scale = (1i64 << (spec.bits_per_sample - 1)) as f32;
                reader
                    .samples::<i32>()
                    .map(|sample| sample.map(|value| value as f32 / scale))
                    .collect::<Result<Vec<_>, _>>()?
            }
        }
    };

    Ok(MonoAudio {
        samples,
        sample_rate: spec.sample_rate,
    })
}

fn write_wav(
    path: impl AsRef<Path>,
    sample_rate: u32,
    samples: &[f32],
) -> Result<(), Box<dyn Error>> {
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate,
        bits_per_sample: 32,
        sample_format: hound::SampleFormat::Float,
    };
    let mut writer = hound::WavWriter::create(path, spec)?;
    for sample in samples {
        writer.write_sample(sample.clamp(-1.0, 1.0))?;
    }
    writer.finalize()?;
    Ok(())
}
