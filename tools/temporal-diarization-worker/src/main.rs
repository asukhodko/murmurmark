use diarization::{
    embed::EmbedModel,
    offline::{OwnedDiarizationPipeline, OwnedPipelineOptions},
    plda::PldaTransform,
    reconstruct::spans_to_rttm_lines,
    segment::SegmentModel,
};
use serde_json::{Value, json};
use std::{collections::BTreeMap, fs, path::Path};

fn read_wav(path: &Path) -> Result<Vec<f32>, Box<dyn std::error::Error>> {
    let mut reader = hound::WavReader::open(path)?;
    let spec = reader.spec();
    if spec.sample_rate != 16_000 || spec.channels != 1 {
        return Err(format!("expected mono 16 kHz WAV, got {} Hz / {} channels", spec.sample_rate, spec.channels).into());
    }
    match (spec.sample_format, spec.bits_per_sample) {
        (hound::SampleFormat::Int, 16) => Ok(reader
            .samples::<i16>()
            .map(|sample| sample.map(|value| value as f32 / i16::MAX as f32))
            .collect::<Result<Vec<_>, _>>()?),
        (hound::SampleFormat::Float, 32) => {
            Ok(reader.samples::<f32>().collect::<Result<Vec<_>, _>>()?)
        }
        _ => Err("unsupported WAV sample format".into()),
    }
}

fn parse_rttm(lines: Vec<String>) -> Result<Vec<Value>, Box<dyn std::error::Error>> {
    let mut parsed = Vec::new();
    for line in lines {
        let fields: Vec<&str> = line.split_whitespace().collect();
        if fields.len() < 8 || fields[0] != "SPEAKER" {
            return Err(format!("unexpected RTTM line: {line}").into());
        }
        let start: f64 = fields[3].parse()?;
        let duration: f64 = fields[4].parse()?;
        parsed.push((start, start + duration, fields[7].to_owned()));
    }
    parsed.sort_by(|left, right| {
        left.0
            .total_cmp(&right.0)
            .then_with(|| left.1.total_cmp(&right.1))
            .then_with(|| left.2.cmp(&right.2))
    });
    let mut canonical = BTreeMap::new();
    for (_, _, speaker) in &parsed {
        let next = canonical.len();
        canonical.entry(speaker.clone()).or_insert(next);
    }
    Ok(parsed
        .into_iter()
        .map(|(start, end, speaker)| {
            json!({
                "start": (start * 1_000_000.0).round() / 1_000_000.0,
                "end": (end * 1_000_000.0).round() / 1_000_000.0,
                "speaker": canonical[&speaker],
            })
        })
        .collect())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 4 {
        return Err("usage: worker INPUT.wav MODEL.onnx OUTPUT.json".into());
    }
    let input = Path::new(&args[1]);
    let model = Path::new(&args[2]);
    let output = Path::new(&args[3]);
    let samples = read_wav(input)?;
    if samples.len() < 160_000 {
        return Err("candidate audio must be at least 10 seconds".into());
    }

    let mut segmentation = SegmentModel::bundled()?;
    let mut embedding = EmbedModel::from_file(model)?;
    let plda = PldaTransform::new()?;
    let pipeline = OwnedDiarizationPipeline::with_options(OwnedPipelineOptions::new());
    let result = pipeline.run(&mut segmentation, &mut embedding, &plda, &samples)?;
    let spans = parse_rttm(spans_to_rttm_lines(result.spans_slice(), "remote"))?;
    let payload = json!({
        "schema": "murmurmark.temporal_diarization_worker_result/v1",
        "sample_rate": 16000,
        "duration_sec": (samples.len() as f64 / 16000.0 * 1_000_000.0).round() / 1_000_000.0,
        "cluster_count": result.num_clusters(),
        "span_count": spans.len(),
        "spans": spans,
    });
    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(output, serde_json::to_vec_pretty(&payload)?)?;
    Ok(())
}
