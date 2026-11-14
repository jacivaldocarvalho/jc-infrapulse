# Integração F3C.3.2 e reconciliação OCI

`reconcile_f3c3` verifica o pacote F3C.2 e os bundles F3C.3.2 com
`verify_evidence_with_sboms`, que compartilha a mesma implementação do verificador
existente. Apenas após a validação das assinaturas, política, materiais, IDs e
proveniências, entrega à reconciliação os bytes do SBOM mantidos no snapshot
validado. Substituições posteriores dos arquivos originais não mudam o snapshot.
A API anterior `verify_evidence` continua retornando somente seu resumo.

O chamador fornece política e hash de política, chave, commit, run, repositório,
IDs de imagem, plataforma e distro por canais independentes. O verificador
existente exige handoff, Cosign e Trivy fixados, além de Docker e das imagens
locais para a validação F3B.1. A reconciliação confirma a configuração e os
DiffIDs das camadas fornecidas contra o ID da imagem. O perfil atual exige que
o ID corresponda ao digest da configuração; perfis cujo ID identifica um índice
OCI são rejeitados, sem fallback. O leitor virtual aceita USTAR, a variante de
cabeçalho GNU observada e PAX local apenas para `path`/`linkpath`. Limites:
128 MiB por camada, 256 MiB agregados, 1 MiB por payload PAX e 100.000 entradas.
Gzip, PAX global, chaves PAX desconhecidas e outras extensões TAR continuam
rejeitados sem fallback.

O resultado separa a assinatura de chave confiável do relatório de comparação e
mantém `COVERAGE_PARTIAL`. A assinatura não comprova a identidade do builder,
a independência das fontes, autenticidade da plataforma obtida fora da imagem
ou completude. O pacote histórico foi validado com chaves descartáveis cujos
bundles não foram preservados; esta orquestração ainda não foi executada com
esses bundles históricos. Os testes locais de orquestração usam mocks para os
verificadores; o CI também exercita Cosign real com chaves descartáveis e casos
negativos.

Os archives preservados do run `41238810850` foram conferidos por SHA-256 contra
o manifesto do handoff e percorridos até o inventário final sem carregar imagens
no Docker: backend, 12 camadas, 224.294.912 bytes, 11.095 entradas finais e
9.228 arquivos com hash; nginx, 12 camadas, 56.756.736 bytes, 2.214 entradas e
1.264 arquivos com hash. Os hashes dos configs coincidiram com os IDs do
handoff e todos os DiffIDs com os configs. Isso valida essas imagens e os
formatos observados, sem provar identidade do builder ou cobertura completa.

Os archives de imagem do CI retêm por um dia; relatórios sanitizados, por sete
dias; evidência CI sem assinatura, por 30 dias. A evidência mais longa não inclui
o handoff de camadas nem bundles F3C.3.2 descartáveis. A cobertura permanece
`COVERAGE_PARTIAL`.
