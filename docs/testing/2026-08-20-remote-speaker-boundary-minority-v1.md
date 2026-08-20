# Remote Speaker Boundary and Minority-Voice Segmentation v1 Result

Дата: 2026-08-20
Решение: `KEEP_COVERAGE_V3`

Candidate и implementation были заморожены до чтения controlled-hard и real reference. Terminal
evaluation выполнена один раз; повтор отчёта совпал байт-в-байт. Production guards остались
неизменными.

## Controlled Hard

| Метрика | Результат |
|---|---:|
| Boundary precision | `1.000000` |
| Boundary recall | `0.705882` |
| Boundary F1 | `0.827586` |
| B-cubed F1 | `0.529176` |
| Pairwise precision | `0.435323` |
| Minority-speaker recall | `1.000000` |
| Unknown word ratio | `0.014085` |
| Minimum timing-shift boundary agreement | `0.818182` |
| Minimum timing-shift partition ARI | `0.000000` |
| Word conservation | `1.000000` |

## Real Diagnostic

Независимый машинный reference содержит восемь remote speakers. Это диагностический источник, а не
human-reviewed truth.

| Метрика | Результат |
|---|---:|
| Boundary precision | `0.044688` |
| Boundary recall | `0.670886` |
| Boundary F1 | `0.083794` |
| Candidate partitions / reference speakers | `4 / 8` |
| Speaker-count ratio | `0.500000` |
| B-cubed F1 | `0.475625` |
| Pairwise precision | `0.311401` |
| Minority boundary recall | `0.681818` |
| Minority-speaker recall | `0.017161` |
| Unknown word ratio | `0.004163` |
| Minimum timing-shift partition ARI | `0.289387` |
| Word conservation | `1.000000` |

## Вывод

Спектральные скачки, паузы и текущие segment embeddings не образуют надёжную speaker topology.
Алгоритм находит большую часть смен, но создаёт 1186 candidate boundaries при 79 reference
boundaries, а последующая кластеризация снова сжимает восемь голосов до четырёх и теряет редких
участников. Пороговая доработка этого семейства после открытия terminal truth запрещена.

Coverage v3, selected transcripts, ASR, Echo Guard и raw audio не изменены. Следующий шаг:
пересобрать свежий speaker-resolved corpus на рабочем профиле и заново ранжировать остаточные
ошибки, не предполагая, что segmentation v1 была продвинута.

Tracked evidence: [manifest](remote-speaker-boundary-minority-v1-manifest.json).
