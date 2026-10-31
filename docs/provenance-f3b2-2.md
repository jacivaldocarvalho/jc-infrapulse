# F3B.2.2 — assinatura local e verificação offline

O módulo `scripts/provenance_signature.py` adiciona uma camada criptográfica à
proveniência F3B.1 sem alterar seu contrato. Cosign 3.1.3 assina os bytes exatos
do statement como blob e grava um bundle Sigstore JSON. O verificador primeiro
revalida localmente o statement com F3B.1 (SBOM, manifesto, commit e materiais),
depois confere o bundle usando `cosign verify-blob --offline` e uma chave pública
da política local. O bundle nunca é uma fonte de confiança. O executável deve
corresponder à versão e ao SHA-256 de `security/cosign-tools.json`.

## Confiança e política

A política é um JSON fora do bundle, com este formato mínimo:

```json
{
  "schema_version": 1,
  "keys": {
    "sha256:<SHA-256 dos bytes PEM>": {
      "public_key": "-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----\n",
      "sha256": "<mesmo SHA-256>",
      "status": "ACTIVE"
    }
  }
}
```

O operador deve obter o SHA-256 do arquivo de política por canal independente
e passá-lo em cada verificação. Sem esse pin independente, uma política e chave
substituídas em conjunto não seriam detectadas. `ACTIVE` é a única condição
aceita; rotação adiciona chave nova à política confiável e revogação muda a
antiga para `REVOKED`, atualizando o pin distribuído independentemente. Em modo
offline, um pin antigo não conhece revogações novas: distribuir a política
atualizada é requisito operacional.

## Uso

Gere primeiro SBOM, manifesto e statement pelos comandos F3A/F3B.1. Assine o
arquivo de statement sem alterá-lo:

```sh
python scripts/provenance_signature.py sign \
  --statement /tmp/provenance.json --sbom /tmp/sbom.json \
  --manifest /tmp/manifest.json --trivy /path/to/trivy \
  --cosign /path/to/cosign --private-key /secure/path/cosign.key \
  --bundle /tmp/provenance.sigstore.json
```

Verifique integridade e assinatura, fornecendo o pin da política obtido fora
do pacote de evidências:

```sh
python scripts/provenance_signature.py verify \
  --statement /tmp/provenance.json --sbom /tmp/sbom.json \
  --manifest /tmp/manifest.json --trivy /path/to/trivy \
  --cosign /path/to/cosign --bundle /tmp/provenance.sigstore.json \
  --policy /secure/path/trust-policy.json \
  --policy-sha256 <PIN_CONFIAVEL> --key-id sha256:<FINGERPRINT>
```

Para imagem, inclua `--expected-image-id sha256:...` nos dois comandos. A chave
privada deve ser criada e mantida fora do repositório, criptografada com
passphrase, sob controle de acesso e backup restritos. Nunca coloque a
passphrase em argumentos ou logs; Cosign pode recebê-la por entrada segura ou
`COSIGN_PASSWORD` no ambiente do processo. Assinatura local não identifica por
si só usuário, máquina ou builder.

Os estados são independentes: `INTEGRITY_VERIFIED` vem da validação F3B.1;
`SIGNATURE_VALID` significa que Cosign verificou a assinatura dos bytes exatos;
`TRUSTED_SIGNER` significa que a chave estava ativa na política cujo hash foi
fixado pelo operador. Falha em qualquer etapa encerra com erro, sem fallback
para hashes apenas. Isso não prova completude do SBOM nem identidade confiável
do produtor. Não é alegado nível SLSA. A integração à CI permanece desabilitada;
uma migração futura pode mover a chave para KMS ou usar identidade OIDC e
política de workflow.

As entradas são abertas sem seguir symlinks e limitadas por tamanho. O SBOM,
manifesto e materiais são revalidados antes de assinar/verificar. Saídas são
criadas em temporário restrito e substituídas atomicamente; artefatos devem
ficar fora do checkout. O fluxo não faz downloads, chamadas de rede, scans ou
execução de containers.
