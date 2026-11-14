# F3C.4.2D — reconciliação preliminar de pacotes OCI

O comparador registra diferenças exatas entre coordenadas (ecossistema, namespace,
nome, versão e arquitetura) de duas fontes distintas, vinculadas a ID de imagem e
plataforma esperados pelo chamador. Diferenças de versão aparecem dos dois lados;
elementos não suportados e lacunas explícitas são reportados. Dados duplicados,
inválidos, fora de escopo ou dos limites são rejeitados. O resultado permanece
`COVERAGE_PARTIAL`, inclusive quando os inventários coincidem.

Os adaptadores leem `var/lib/dpkg/status`, `lib/apk/db/installed` e arquivos
explícitos `site-packages/*.dist-info/METADATA` e `node_modules/*/package.json`
das camadas OCI validadas em
memória; não seguem symlinks no host. O leitor PyPI normaliza nomes e aceita um
perfil restrito de versões. Purl Trivy suporta perfis restritos Debian, Alpine e
PyPI e npm; `arch`, `distro` e `epoch` conhecidos são preservados ou comparados com o
distro esperado. Qualificadores e formatos não suportados são lacunas. Diretórios
implícitos, arquivos não reconhecidos, bundles npm, outros bancos e instalações alternativas
não são cobertos. A ausência de entradas não demonstra inexistência de pacotes.

O SBOM passa por hash esperado, schema CycloneDX 1.7 fixado, metadados de versão
Trivy, grafo e identidade da imagem. Os hashes, descritores, ordem das camadas,
nome lógico, ID, distro e plataforma esperados devem vir de fontes independentes.
Identificadores distintos não provam independência; a verificação por hash e a
identidade do root não autenticam o produtor nem vinculam a plataforma.

Para integrar com confiança, a proposta é verificar a assinatura do pacote de
evidências com o verificador Cosign existente e uma chave/política fixada por
canal independente, verificar os hashes e comparar integralmente os statements;
obter plataforma de config/manifest OCI autenticados e confrontá-la com o escopo
esperado. Uma chave temporária ou manifesto não assinado comprova apenas a
integridade no escopo descrito, não a identidade do builder nem nível SLSA. Essa
ligação autenticada e a integração ao verificador ainda não foram implementadas.

Foram inspecionados SBOMs históricos preservados da execução 41238810850. Eles
exercitam `distro`, `epoch`, versões Alpine com underscore e PyPI. Esses arquivos
são evidência de compatibilidade, não uma fonte independente de autenticidade.
As imagens reais têm camadas acima dos limites do leitor USTAR atual, portanto
não foram reconstruídas nesta validação. Permanecem pendentes fixtures derivadas
de camadas reais dentro dos limites, suporte ampliado, autenticação da origem,
vínculo independente de plataforma e revisão de segurança.
