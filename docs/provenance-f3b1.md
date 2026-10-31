# F3B.1 — proveniência local de SBOM

## Local SBOM

O runner `scripts/provenance.py` emite JSON com a estrutura [in-toto Statement v1 e predicate SLSA Provenance v1](https://slsa.dev/spec/v1.0/provenance). O tipo de build desta implementação é a URI desta seção. O sujeito é o SBOM; o manifesto F3A vincula o commit, o hash e a origem. O inventário local e o executável Trivy são registrados como dependências quando disponíveis. Para imagens, `imageId` é um ID local, não um digest de manifesto remoto. O ambiente é `local-untrusted` e `builder` é vazio. O manifesto F3A não fornece identificador de invocação, portanto nenhum é inventado.

No ambiente isolado descrito em `security/sbom-tools.lock`, gere o manifesto do SBOM com o runner F3A, redirecionando seu stdout para um arquivo fora do checkout. Em seguida execute:

```
python scripts/provenance.py generate --sbom /tmp/sbom.json --manifest /tmp/manifest.json --trivy /path/to/trivy --statement /tmp/provenance.json
python scripts/provenance.py verify --sbom /tmp/sbom.json --manifest /tmp/manifest.json --trivy /path/to/trivy --statement /tmp/provenance.json
```

O verificador exige o mesmo commit no checkout, compara o manifesto e os hashes dos arquivos locais, valida o SBOM e a versão e o hash do Trivy. O resultado `INTEGRITY_VERIFIED` indica consistência dos dados disponíveis; `NOT_VERIFIED` indica que a autenticidade não foi estabelecida. A verificação falha se materiais não estiverem acessíveis ou tiverem mudado.

Para imagens, forneça em **ambos** os comandos `--expected-image-id sha256:...`, obtido independentemente do SBOM e manifesto, de uma fonte em que o operador confie. O ID deve ser completo e estar disponível no Docker local. O verificador resolve esse ID, regenera o SBOM offline com o Trivy fixado usando somente Docker e compara componentes e grafo de dependências; não executa containers nem faz pull. Um ID de imagem local não é um digest de manifesto de registry. A comparação preserva multiplicidade, propriedades e identificadores; `bom-ref` aleatório é substituído pela identidade completa do componente e as relações são traduzidas para essas identidades. Ordem de listas, timestamps e metadados globais não integram o inventário comparado. Referências ambíguas, relações não suportadas e diferenças falham. O custo inclui leitura da imagem e geração temporária de outro SBOM a cada execução; não é um scan de vulnerabilidades.

A correspondência significa que ambos os inventários foram detectados pelo mesmo Trivy e versão na imagem local esperada; não comprova detecção completa, integridade da origem do ID, assinatura, identidade do produtor ou autenticidade. Resultados de imagem permanecem `COVERAGE_PARTIAL`. O Docker daemon, Trivy e ambiente locais devem ser confiáveis para essa comparação.

Para inventários de arquivos, somente os caminhos absolutos exatos `backend/requirements.lock` e `frontend/package-lock.json` dentro do checkout são aceitos. O verificador recusa symlinks e arquivos especiais, limita o tamanho a 10 MiB, abre o material por descritores sem seguir links e recalcula a cobertura F3A; uma divergência de tipo, componentes ou cobertura falha. A validação por descritores reduz corridas de troca de caminho, mas não transforma um checkout controlado por outra pessoa em fonte confiável. Outros locks e inventários genéricos não são suportados nesta etapa.

O statement é produzido depois da geração, com base em um manifesto não assinado. Não prova que o processo relatado ocorreu, que o binário verificado foi o usado na geração, que todos os materiais foram capturados, nem que o ambiente ou builder é confiável. Não há assinatura, atestação autenticada ou alegação de nível SLSA. F3B.2 dependerá de identidade e assinatura verificáveis; F3C dependerá de integração CI e gestão de artefatos, fora deste escopo.
