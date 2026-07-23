#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <speex/speex_echo.h>

typedef struct {
    float *samples;
    uint32_t sample_rate;
    uint64_t frames;
} MonoAudio;

static uint16_t read_u16_le(FILE *file) {
    uint8_t bytes[2];
    if (fread(bytes, 1, 2, file) != 2) {
        return 0;
    }
    return (uint16_t)bytes[0] | ((uint16_t)bytes[1] << 8);
}

static uint32_t read_u32_le(FILE *file) {
    uint8_t bytes[4];
    if (fread(bytes, 1, 4, file) != 4) {
        return 0;
    }
    return (uint32_t)bytes[0] |
           ((uint32_t)bytes[1] << 8) |
           ((uint32_t)bytes[2] << 16) |
           ((uint32_t)bytes[3] << 24);
}

static void write_u16_le(FILE *file, uint16_t value) {
    uint8_t bytes[2] = {
        (uint8_t)(value & 0xff),
        (uint8_t)((value >> 8) & 0xff),
    };
    fwrite(bytes, 1, 2, file);
}

static void write_u32_le(FILE *file, uint32_t value) {
    uint8_t bytes[4] = {
        (uint8_t)(value & 0xff),
        (uint8_t)((value >> 8) & 0xff),
        (uint8_t)((value >> 16) & 0xff),
        (uint8_t)((value >> 24) & 0xff),
    };
    fwrite(bytes, 1, 4, file);
}

static int skip_bytes(FILE *file, uint32_t bytes) {
    if (fseek(file, bytes + (bytes & 1u), SEEK_CUR) != 0) {
        return -1;
    }
    return 0;
}

static float clamp_float(float value) {
    if (value > 1.0f) {
        return 1.0f;
    }
    if (value < -1.0f) {
        return -1.0f;
    }
    return value;
}

static spx_int16_t float_to_i16(float value) {
    float clamped = clamp_float(value);
    if (clamped >= 0) {
        return (spx_int16_t)(clamped * 32767.0f);
    }
    return (spx_int16_t)(clamped * 32768.0f);
}

static float i16_to_float(spx_int16_t value) {
    return (float)value / 32768.0f;
}

static int read_wav(const char *path, MonoAudio *audio) {
    memset(audio, 0, sizeof(*audio));

    FILE *file = fopen(path, "rb");
    if (!file) {
        fprintf(stderr, "cannot open %s: %s\n", path, strerror(errno));
        return -1;
    }

    char id[4];
    if (fread(id, 1, 4, file) != 4 || memcmp(id, "RIFF", 4) != 0) {
        fprintf(stderr, "expected RIFF WAV: %s\n", path);
        fclose(file);
        return -1;
    }
    (void)read_u32_le(file);
    if (fread(id, 1, 4, file) != 4 || memcmp(id, "WAVE", 4) != 0) {
        fprintf(stderr, "expected WAVE file: %s\n", path);
        fclose(file);
        return -1;
    }

    uint16_t audio_format = 0;
    uint16_t channels = 0;
    uint32_t sample_rate = 0;
    uint16_t bits_per_sample = 0;
    uint32_t data_size = 0;
    long data_offset = -1;

    while (fread(id, 1, 4, file) == 4) {
        uint32_t chunk_size = read_u32_le(file);
        long chunk_start = ftell(file);

        if (memcmp(id, "fmt ", 4) == 0) {
            audio_format = read_u16_le(file);
            channels = read_u16_le(file);
            sample_rate = read_u32_le(file);
            (void)read_u32_le(file);
            (void)read_u16_le(file);
            bits_per_sample = read_u16_le(file);
            long consumed = ftell(file) - chunk_start;
            if (consumed < (long)chunk_size && skip_bytes(file, (uint32_t)((long)chunk_size - consumed)) != 0) {
                fprintf(stderr, "cannot skip fmt extension in %s\n", path);
                fclose(file);
                return -1;
            }
        } else if (memcmp(id, "data", 4) == 0) {
            data_size = chunk_size;
            data_offset = ftell(file);
            if (skip_bytes(file, chunk_size) != 0) {
                fprintf(stderr, "cannot skip data in %s\n", path);
                fclose(file);
                return -1;
            }
        } else if (skip_bytes(file, chunk_size) != 0) {
            fprintf(stderr, "cannot skip WAV chunk in %s\n", path);
            fclose(file);
            return -1;
        }
    }

    if (channels != 1 || sample_rate == 0 || data_offset < 0) {
        fprintf(stderr, "expected mono WAV with data chunk: %s\n", path);
        fclose(file);
        return -1;
    }

    uint64_t frames = 0;
    if (audio_format == 3 && bits_per_sample == 32) {
        frames = data_size / sizeof(float);
        audio->samples = (float *)calloc((size_t)frames, sizeof(float));
        if (!audio->samples) {
            fclose(file);
            return -1;
        }
        fseek(file, data_offset, SEEK_SET);
        if (fread(audio->samples, sizeof(float), (size_t)frames, file) != frames) {
            fprintf(stderr, "cannot read float samples from %s\n", path);
            fclose(file);
            free(audio->samples);
            return -1;
        }
    } else if (audio_format == 1 && bits_per_sample == 16) {
        frames = data_size / sizeof(int16_t);
        audio->samples = (float *)calloc((size_t)frames, sizeof(float));
        if (!audio->samples) {
            fclose(file);
            return -1;
        }
        fseek(file, data_offset, SEEK_SET);
        for (uint64_t index = 0; index < frames; index++) {
            int16_t value = (int16_t)read_u16_le(file);
            audio->samples[index] = i16_to_float(value);
        }
    } else {
        fprintf(stderr, "unsupported WAV format in %s: format=%u bits=%u\n", path, audio_format, bits_per_sample);
        fclose(file);
        return -1;
    }

    audio->sample_rate = sample_rate;
    audio->frames = frames;
    fclose(file);
    return 0;
}

static int write_wav_f32(const char *path, const MonoAudio *audio) {
    FILE *file = fopen(path, "wb");
    if (!file) {
        fprintf(stderr, "cannot create %s: %s\n", path, strerror(errno));
        return -1;
    }

    uint32_t data_size = (uint32_t)(audio->frames * sizeof(float));
    uint32_t riff_size = 4 + (8 + 16) + (8 + data_size);

    fwrite("RIFF", 1, 4, file);
    write_u32_le(file, riff_size);
    fwrite("WAVE", 1, 4, file);

    fwrite("fmt ", 1, 4, file);
    write_u32_le(file, 16);
    write_u16_le(file, 3);
    write_u16_le(file, 1);
    write_u32_le(file, audio->sample_rate);
    write_u32_le(file, audio->sample_rate * sizeof(float));
    write_u16_le(file, sizeof(float));
    write_u16_le(file, 32);

    fwrite("data", 1, 4, file);
    write_u32_le(file, data_size);
    if (fwrite(audio->samples, sizeof(float), (size_t)audio->frames, file) != audio->frames) {
        fprintf(stderr, "cannot write samples to %s\n", path);
        fclose(file);
        return -1;
    }

    fclose(file);
    return 0;
}

static void free_audio(MonoAudio *audio) {
    free(audio->samples);
    audio->samples = NULL;
    audio->frames = 0;
}

int main(int argc, char **argv) {
    if (argc < 4 || argc > 7) {
        fprintf(stderr, "usage: %s mic.wav remote.wav out.wav [frame_ms] [tail_ms] [signed_delay_ms]\n", argv[0]);
        return 2;
    }

    int frame_ms = argc >= 5 ? atoi(argv[4]) : 20;
    int tail_ms = argc >= 6 ? atoi(argv[5]) : 500;
    double delay_ms = argc >= 7 ? strtod(argv[6], NULL) : 0.0;
    if (frame_ms <= 0 || tail_ms <= 0 || !isfinite(delay_ms)) {
        fprintf(stderr, "frame_ms/tail_ms must be positive and signed_delay_ms must be finite\n");
        return 2;
    }

    MonoAudio mic;
    MonoAudio remote;
    if (read_wav(argv[1], &mic) != 0) {
        return 1;
    }
    if (read_wav(argv[2], &remote) != 0) {
        free_audio(&mic);
        return 1;
    }
    if (mic.sample_rate != remote.sample_rate) {
        fprintf(stderr, "mic and remote sample rates differ\n");
        free_audio(&mic);
        free_audio(&remote);
        return 1;
    }

    uint32_t sample_rate = mic.sample_rate;
    int64_t delay_samples = (int64_t)llround(delay_ms * (double)sample_rate / 1000.0);
    int frame_size = (int)((uint64_t)sample_rate * (uint64_t)frame_ms / 1000u);
    int filter_length = (int)((uint64_t)sample_rate * (uint64_t)tail_ms / 1000u);
    if (frame_size <= 0 || filter_length <= frame_size) {
        fprintf(stderr, "invalid SpeexDSP frame/filter sizes\n");
        free_audio(&mic);
        free_audio(&remote);
        return 1;
    }

    MonoAudio out;
    out.sample_rate = sample_rate;
    out.frames = mic.frames;
    out.samples = (float *)calloc((size_t)out.frames, sizeof(float));
    if (!out.samples) {
        free_audio(&mic);
        free_audio(&remote);
        return 1;
    }

    SpeexEchoState *state = speex_echo_state_init(frame_size, filter_length);
    if (!state) {
        fprintf(stderr, "cannot initialize SpeexDSP echo state\n");
        free_audio(&mic);
        free_audio(&remote);
        free_audio(&out);
        return 1;
    }
    int rate = (int)sample_rate;
    speex_echo_ctl(state, SPEEX_ECHO_SET_SAMPLING_RATE, &rate);

    spx_int16_t *rec = (spx_int16_t *)calloc((size_t)frame_size, sizeof(spx_int16_t));
    spx_int16_t *play = (spx_int16_t *)calloc((size_t)frame_size, sizeof(spx_int16_t));
    spx_int16_t *clean = (spx_int16_t *)calloc((size_t)frame_size, sizeof(spx_int16_t));
    if (!rec || !play || !clean) {
        speex_echo_state_destroy(state);
        free(rec);
        free(play);
        free(clean);
        free_audio(&mic);
        free_audio(&remote);
        free_audio(&out);
        return 1;
    }

    for (uint64_t pos = 0; pos < mic.frames; pos += (uint64_t)frame_size) {
        memset(rec, 0, (size_t)frame_size * sizeof(spx_int16_t));
        memset(play, 0, (size_t)frame_size * sizeof(spx_int16_t));
        int actual = frame_size;
        if (pos + (uint64_t)actual > mic.frames) {
            actual = (int)(mic.frames - pos);
        }
        for (int index = 0; index < actual; index++) {
            rec[index] = float_to_i16(mic.samples[pos + (uint64_t)index]);
            int64_t destination = (int64_t)(pos + (uint64_t)index);
            int64_t source = destination - delay_samples;
            if (source >= 0 && (uint64_t)source < remote.frames) {
                play[index] = float_to_i16(remote.samples[(uint64_t)source]);
            }
        }
        speex_echo_cancellation(state, rec, play, clean);
        for (int index = 0; index < actual; index++) {
            out.samples[pos + (uint64_t)index] = i16_to_float(clean[index]);
        }
    }

    int status = write_wav_f32(argv[3], &out);

    speex_echo_state_destroy(state);
    free(rec);
    free(play);
    free(clean);
    free_audio(&mic);
    free_audio(&remote);
    free_audio(&out);
    return status == 0 ? 0 : 1;
}
