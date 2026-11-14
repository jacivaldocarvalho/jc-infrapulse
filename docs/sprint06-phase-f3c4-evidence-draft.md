# F3C.4.3 e F3C.4.4 — preparação de evidências

O relatório preliminar em `scripts/oci_reconciliation_report.py` versiona um
resumo de comparação e hashes de bytes fornecidos pelo chamador. Ele registra
explicitamente que os bytes não foram vinculados à comparação, que origem,
independência e plataforma não foram verificadas e que não há prova de cobertura
completa. Os hashes são rotulados como não verificados. O estado é sempre
`COVERAGE_PARTIAL`; este objeto não altera o contrato de cobertura v1 e não é
evidência de origem, integridade autenticada ou aprovação operacional.

A suíte desse relatório roda no job existente `security-foundations`. Isso valida
o comportamento local do resumo, não integra o relatório aos gates de cobertura.
Para concluir as fases ainda é necessário integrar o pacote de evidências
assinado existente, definir contrato e migração
revisados, executar testes negativos de ponta a ponta e definir retenção e gates.
Nenhuma dessas garantias é inferida de um objeto ou booleano fornecido pelo chamador.

Um fluxo experimental, `oci_reconciliation_pipeline.py`, reconstrói a
comparação a partir dos bytes das camadas e do SBOM, verificando o digest da
configuração, plataforma `linux/amd64` e ordem dos DiffIDs. Sua saída tem schema
próprio em `security/oci-reconciliation-report-v1.schema.json`, com estados fixos
de origem e independência não verificadas e lacunas obrigatórias. O schema não
prova assinaturas nem autenticidade; o manifesto, distro e produtor permanecem
responsabilidade de fontes independentes. O fluxo é testado no job de segurança,
mas não publica evidências nem modifica gates ou o contrato de cobertura v1.

`compare_signed_image` verifica assinaturas Cosign dos bytes exatos do manifesto
OCI direto e do SBOM, usando chave, política e hash de política fixados fora do
pacote. Confere os descritores contra configuração e camadas e verifica os
DiffIDs e a plataforma. As assinaturas exigidas são bundles separados para esses
dois blobs; os artefatos históricos assinados de F3C.3.2 usam outro empacotamento
e ainda não estão integrados a essa função. A assinatura prova autorização da
chave sobre bytes, não independência, identidade do builder ou completude. Os
testes locais dessa orquestração substituem o verificador criptográfico por mock;
validação real com Cosign e chaves descartáveis continua pendente.

Inspeção somente leitura dos arquivos OCI históricos da execução 41238810850:
as maiores entradas candidatas têm 100.435.968 bytes (backend) e 41.925.632
bytes (frontend), acima do limite de 16 MiB por camada. Entre blobs candidatos
USTAR de até 16 MiB, o leitor aceitou 8 no backend e 8 no frontend; rejeitou 1
por tipo TAR não suportado e 3 por caminho não normalizado, respectivamente. Isso
não valida a imagem inteira ou a origem dos arquivos, nem substitui o teste
integrado com imagens e assinaturas autenticadas.

O job `security-foundations` também executa `validate_signed_oci_fixture.py` em
runner GitHub hospedado. A rotina cria uma imagem OCI mínima em memória e duas
chaves temporárias, instala Cosign a partir do pin existente e testa as assinaturas
de manifesto e SBOM, além de sete casos negativos. Nenhuma chave ou assinatura é
publicada; o diretório temporário é removido ao final. Esse teste não modifica os
gates de cobertura e não substitui a integração do pacote F3C.3.2 ou a validação de
imagens reais. O resultado dessa execução deve ser consultado no Actions do PR.
