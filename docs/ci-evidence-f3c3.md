# F3C.3.1 — assinatura local do pacote CI

`scripts/ci_evidence_signature.py` assina um pacote F3C.2 já baixado. No modo
padrão, a saída contém cinco bundles Sigstore JSON independentes: um para os
bytes exatos de `evidence-manifest.json` e um para cada statement (`python`,
`npm`, `backend-image`, `nginx-image`). O manifesto, os statements e os SBOMs
não são reescritos. Antes de assinar, o CLI valida o conjunto exato de arquivos, o
sidecar e hashes/tamanhos, commit, repositório, run ID/attempt, cobertura,
materiais, identidade OCI local e contratos F3B.1. Cada bundle criado é
verificado contra a política independente antes da publicação atômica do
diretório de saída. A verificação repete a validação do pacote e verifica os
cinco bundles; exige o checkout correspondente e, para imagens, Docker, Trivy
fixado e as imagens locais para a regeneração F3B.1.

Para imagens, `--handoff-dir` aponta para o diretório `docker-build` exportado
pelo job F3C.2. O CLI exige exatamente `manifest.json`, `backend-image.tar` e
`nginx-image.tar`; calcula SHA-256 e tamanho lendo os bytes reais e compara os
valores com o manifesto do handoff e os materiais do pacote de evidências. A
validação estática aceita cabeçalhos USTAR do Docker save clássico e o perfil
OCI observado no `docker image save` 29.9.2. Uma pré-passagem limitada verifica
checksum, offsets, tamanhos, alinhamento e terminadores antes de `tarfile`;
rejeita PAX/GNU, links, tipos especiais, paths ambíguos e arquivos truncados.
Diretórios TAR são comparados com o nome canônico apresentado por `tarfile`, mas
nomes de arquivos permanecem sem normalização. `tarfile` precisa interpretar
exatamente os mesmos membros pré-validados.

No perfil OCI suportado, `oci-layout`, `index.json` e o `manifest.json` de
compatibilidade Docker ficam na raiz. São aceitas duas formas observadas. No
perfil aninhado, o índice raiz contém exatamente um descriptor de índice OCI
aninhado; seu digest validado contra o blob é o image ID local fornecido
independentemente pelo Docker daemon. O índice aninhado contém exatamente um
manifest de imagem para uma plataforma e um manifest de attestation Docker
relacionado. No perfil direto, o índice raiz contém exatamente um descriptor
de manifest, sem campo `platform`; o descriptor é validado contra os bytes do
manifest. Nesse perfil, o image ID independente deve ser exatamente o digest
do config, não o digest do índice nem do manifest. O config deve declarar
`linux/amd64`. Ambos os perfis validam digests e tamanhos de config/layers, os
DiffIDs na ordem declarada e a concordância do `manifest.json` de compatibilidade
Docker. O perfil direto aceita apenas layers OCI TAR não comprimidas e valida
também `LayerSources`; o perfil aninhado aceita layers gzip e verifica seu
conteúdo descomprimido. O payload da attestation aninhada é conferido por
digest/tamanho, sem ser interpretado como evidência de proveniência.

Os perfis são intencionalmente estreitos: uma imagem/plataforma, índice raiz
com um único descriptor, tipos de layer definidos acima e, no perfil aninhado,
a attestation no layout observado. URLs externas, descriptors desconhecidos,
blobs não referenciados ou entradas extras são rejeitados. OCI multi-plataforma,
outras variações de layout e formatos não observados falham fechados. Não executa
`docker load`, não inicia containers e não faz pull. O limite é
2 GiB por archive, 4 GiB no total, 100.000 membros, 2 GiB de conteúdo expandido
por archive e 16 MiB para config/manifest. Cada archive é lido para hash e
validação; isto acrescenta I/O e CPU proporcional ao tamanho (até duas leituras,
sem cópia permanente adicional).

A validação F3B.1 ainda consulta o Docker daemon local para resolver o image ID
esperado e regenerar/comparar o inventário via Trivy. O daemon e o host do
operador são, portanto, fronteiras de confiança: mantenha-os sob controle e
use apenas imagens locais esperadas. O archive recebido é dado não confiável e
é analisado sem ser carregado no daemon; hashes do manifest, isoladamente, não
autorizam o conteúdo.

O `sign-blob` é chamado com `--tlog-upload=false` e
`--use-signing-config=false`: usa a chave local sem enviar entrada ao Rekor ou
buscar configuração TUF de serviços. Essas flags constam no
[contrato Cosign v3.1.3](https://github.com/sigstore/cosign/blob/v3.1.3/cmd/cosign/cli/options/sign.go#L742-L799).
Na verificação, `verify-blob` usa `--offline`, `--insecure-ignore-tlog` e a
chave pública materializada da política independente cujo fingerprint foi
validado. O bypass limita-se à prova de inclusão no transparency log: Cosign
continua verificando a assinatura criptográfica dos bytes exatos com essa
chave. Como a assinatura não é enviada ao Rekor, não há prova de inclusão,
timestamp/transparência pública ou verificabilidade pública; `TRUSTED_SIGNER`
significa confiança local no fingerprint autorizado, não confiança em um log
ou identidade externa do operador.

## Canonicalização local opcional

O modo padrão continua exigindo archives originais que passem no parser OCI
estrito. Para handoffs históricos com somente os JSON Docker legados órfãos
reconhecidos pelo exportador, passe `--canonicalize-oci` tanto a `sign` quanto
`verify`. Não substitua nem edite o handoff ou os arquivos F3C.2.

Nesse modo, cada operação primeiro verifica os SHA-256 e tamanhos dos archives
originais contra o manifesto do handoff e os materiais do pacote F3C.2. Em
seguida, executa `oci_canonicalizer` em temporários privados, fornece os image
IDs esperados explicitamente, revalida os archives canônicos e confirma seus
bytes efetivos. O recibo determinístico
`image-archive-transformation.json` registra commit, run, attempt, hash exato do
manifesto de evidências, hash do manifesto original do handoff e, por imagem,
identidade, perfil, transformação, hashes/tamanhos original e canônico e blobs
removidos. O recibo e `image-archive-transformation.sigstore.json` são publicados
junto aos bundles, sem reescrever o manifesto ou os statements históricos.

O Cosign assina os bytes exatos do recibo com a mesma chave e política dos
demais objetos. `verify --canonicalize-oci` refaz a canonicalização a partir dos
archives originais, confere o recibo armazenado byte a byte e verifica sua
assinatura. Ausência de archive, divergência de hash/tamanho, ID, perfil,
execução/run identity ou recibo causa falha; não há fallback para o modo
estrito. Sem a opção, o verificador rejeita a pasta de assinatura que contenha
o recibo adicional.

```sh
python scripts/ci_evidence_signature.py sign ... --canonicalize-oci
python scripts/ci_evidence_signature.py verify ... --canonicalize-oci
```

Este registro vincula a transformação ao pacote F3C.2 original, mas não altera
seus materiais históricos nem prova compatibilidade com `docker load`.

## Preparação e uso

Obtenha o artifact `ci-evidence-<commit>-<attempt>` do run pretendido e
extraia-o em diretório dedicado. Use o Cosign 3.1.3 verificado por
`security/cosign-tools.json`, Trivy verificado pelo inventário F2 e o checkout
exato do commit esperado. Os image IDs devem vir de uma fonte local independente
(por exemplo, `docker image inspect` executado pelo operador); não os copie do
SBOM ou manifesto. Não faça pull nem execute a imagem. A validação de imagem
exige que o image ID esteja disponível localmente.

Passe explicitamente commit, repositório, run ID, attempt e ambos os image IDs.
`--policy-sha256` é o hash da política confiável obtido por canal independente;
`--key-id` é seu fingerprint. A chave privada entra apenas por caminho externo
controlado pelo operador. A ferramenta cria snapshots temporários restritos;
proteja o host e remova esses temporários após a operação. Nunca coloque chave,
senha ou política apenas dentro do pacote assinado. A saída deve ser um caminho
novo fora do pacote de entrada. O verificador usa a política independente e a
verificação Cosign offline; não confia em chave embutida no bundle.

```sh
python scripts/ci_evidence_signature.py sign \
  --evidence-dir /secure/run/evidence \
  --handoff-dir /secure/run/docker-build \
  --signature-dir /secure/run/signatures \
  --private-key /secure/keys/cosign.key \
  --cosign /verified/cosign --trivy /verified/trivy \
  --policy /secure/trust/trust-policy.json \
  --policy-sha256 <PIN_INDEPENDENTE> --key-id sha256:<FINGERPRINT> \
  --expected-commit <40_HEX> --expected-run-id <RUN_ID> \
  --expected-run-attempt <ATTEMPT> \
  --expected-repository jacivaldocarvalho/jc-infrapulse \
  --expected-backend-image-id sha256:<64_HEX> \
  --expected-nginx-image-id sha256:<64_HEX>

python scripts/ci_evidence_signature.py verify \
  --evidence-dir /secure/run/evidence \
  --handoff-dir /secure/run/docker-build \
  --signature-dir /secure/run/signatures \
  --cosign /verified/cosign --trivy /verified/trivy \
  --policy /secure/trust/trust-policy.json \
  --policy-sha256 <PIN_INDEPENDENTE> --key-id sha256:<FINGERPRINT> \
  --expected-commit <40_HEX> --expected-run-id <RUN_ID> \
  --expected-run-attempt <ATTEMPT> \
  --expected-repository jacivaldocarvalho/jc-infrapulse \
  --expected-backend-image-id sha256:<64_HEX> \
  --expected-nginx-image-id sha256:<64_HEX>
```

Não use o sucesso da auditoria informativa como ausência de vulnerabilidades.
Python/npm permanecem `COVERAGE_PASS`; inventários de imagem permanecem
`COVERAGE_PARTIAL`. A igualdade do inventário regenerado não prova completude.
Os estados `INTEGRITY_VERIFIED`, `SIGNATURE_VALID` e `TRUSTED_SIGNER` são
distintos: assinatura/autorização não estabelecem completude, identidade do
produtor, builder confiável, autenticidade do processo de geração ou nível
SLSA. A F3C.2 continua sem assinatura automática na CI.

## Confiança, operação e limites

Distribua a política pública e seu SHA-256 por canal independente. Somente
chaves `ACTIVE` são aceitas; para revogar/rotacionar, atualize a política e
redistribua seu novo pin. Verificação offline não conhece uma revogação que não
tenha sido distribuída. Guarde a chave privada fora do repositório, sob controle
de acesso, backup e rotação operacional; esta etapa não cria nem protege chaves
operacionais. Assinar exige cinco invocações de assinatura e verificações
locais; validar imagens também repete a regeneração de inventário F3B.1 e pode
consumir CPU/tempo. Não há publicação de artefatos nem execução de containers;
Docker é usado somente para inspeção da imagem local pela validação F3B.1.

Arquivos de evidência são abertos sem seguir symlinks, limitados a 64 MiB cada
e 128 MiB no total, e copiados para snapshots antes do uso. O diretório de
publicação e seus ancestrais devem pertencer a root ou ao usuário atual e não
ser graváveis por grupo/outros, exceto diretórios sticky como `/tmp`; todos
são abertos sem seguir symlinks e os descritores permanecem abertos. O pai é
revalidado antes e depois da publicação;
bundles temporários usam permissões 0700/0600 e são publicados em conjunto por
rename atômico sem sobrescrita. Falhas removem os temporários próprios. Execute
em diretório local controlado e sem escritores concorrentes: a proteção por
descritor impede redirecionamento para outro diretório após validação, mas não
sustenta segurança contra processos com o mesmo UID ou privilégios equivalentes;
esses permanecem dentro da fronteira de confiança do operador. Substituições da
origem no instante do rename falham, o objeto deslocado é restaurado sem
sobrescrever entradas e os arquivos do diretório temporário aberto são limpos.
Os image IDs
são identificadores locais imutáveis; não equivalem a digests de manifesto de
registry. A assinatura liga bytes e identidade declarada, não atesta que o
scanner encontrou todos os componentes.

## Estado da validação desta implementação

Ruff não estava disponível no ambiente de validação local; sua execução permanece
pendente. A integração end-to-end real F3B.1 + Cosign também permanece não
validada, pois não havia ambiente Docker/VM isolado adequado. Os 40/40 testes
unitários locais PASS não substituem essa integração. Este marco é uma
implementação técnica, não uma liberação operacional.
