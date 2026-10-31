# F3A — SBOM local

Resultado: cobertura parcial das imagens; inventários declarados Python e npm validados. Os SBOMs e manifestos estão em `/tmp/infrapulse-f3a-images/` nesta execução.

Compose padrão: nove referências resolvidas, nove disponíveis e analisadas. Os IDs abaixo são IDs locais de imagem, não digests de manifesto verificados. A detecção Trivy não comprova completude; não há scans de vulnerabilidades.

| Fonte | Estado | Componentes | Bytes | Segundos | Image ID ou SHA-256 do SBOM |
|---|---|---:|---:|---:|---|
| grafana/grafana:12.1.0 | COVERAGE_PARTIAL | 585 | 623867 | 7.911 | `sha256:6ac590e7cabc2fbe8d7b8fc1ce9c9f0582177b334e0df9c927ebd9670469440f` |
| grafana/tempo:2.8.2 | COVERAGE_PARTIAL | 353 | 376017 | 2.299 | `sha256:0ef775495967cd5d7a6b2e146b6ea695d624803c8db8349fb8ce4164f719f9b7` |
| jc-infrapulse-api | COVERAGE_PARTIAL | 129 | 244904 | 3.264 | `sha256:4507ec153d641a0c5115362ae94c16b897ef9cc03832e7498f3aa917e099b009` |
| jc-infrapulse-frontend | COVERAGE_PARTIAL | 72 | 126740 | 0.594 | `sha256:c5b5bb3c7630ce58b9e762cec943f0506930d01efc077fc99b9b53782810aacf` |
| jc-infrapulse-worker | COVERAGE_PARTIAL | 129 | 244934 | 0.777 | `sha256:eac7ee50670a63f29c8a8d5c1c5b20e873518a0de9d89563d71b107f46faa1cc` |
| package-lock.json | COVERAGE_PASS | 392 | 274435 | 0.778 | `1891e83dc545e6e9d2d671a5bc211905a84d5024fede876f455b4a34cd910716` |
| otel/opentelemetry-collector-contrib:0.128.0 | COVERAGE_PARTIAL | 977 | 1111949 | 5.672 | `sha256:1ab0baba0ee3695d823c46653d8a6e8894896e668ce8bd7ebe002e948d827bc7` |
| postgres:17-bookworm | COVERAGE_PARTIAL | 150 | 334154 | 7.201 | `sha256:839fbb5a4942a7758dd93e18b42ea5649807491a0545b5eeeb8f0b8bb11857a5` |
| prom/alertmanager:v0.28.1 | COVERAGE_PARTIAL | 157 | 153809 | 1.857 | `sha256:27c475db5fb156cab31d5c18a4251ac7ed567746a2483ff264516437a39b15ba` |
| prom/prometheus:v3.5.0 | COVERAGE_PARTIAL | 421 | 413501 | 4.96 | `sha256:63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996` |
| requirements.lock | COVERAGE_PASS | 49 | 22834 | 0.415 | `3b4c36d7928f55e3d931d5f50eb5154495447e263df794fc4b737c8116a2c612` |

Os manifestos individuais registram SHA-256 dos SBOMs, commit, ferramenta e tempo; o consolidado é `consolidated.json`. Python representa `requirements.lock`; npm inclui dev e colapsa instalações repetidas de mesma identidade. Os inventários declarados não representam necessariamente o conteúdo das imagens.

Trivy 0.76.1 foi verificado pelo hash fixado. Ambiente isolado instalado com `pip --require-hashes` a partir de `security/sbom-tools.lock`, seguido de `pip check`. Schemas CycloneDX 1.7 e referências são fixados por SHA-256. Não há afirmação de build reproduzível. Retenção sugerida para futura integração CI: 30 dias.

A procedência oficial dos schemas não foi comprovada independentemente na revisão offline; a verificação realizada confirmou apenas a correspondência com os hashes fixados localmente.
