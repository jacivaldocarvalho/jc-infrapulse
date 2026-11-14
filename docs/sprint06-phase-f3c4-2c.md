# F3C.4.2C — inventário virtual OCI

`scripts/oci_image_inventory.py` oferece `inventory(layers)` para camadas USTAR
não comprimidas e previamente identificadas. O chamador deve validar por uma
fonte independente o manifesto, a ordem e os descritores; o leitor verifica os
bytes, hashes, limites e sintaxe antes da reconstrução. Cada entrada explícita
remanescente informa caminho, tipo, tamanho, modo, UID/GID, alvo, camada e, para
arquivos e hardlinks, SHA-256 e a origem do conteúdo. A saída é ordenada e não
contém os bytes dos arquivos.

Hardlinks usam tamanho, modo, UID/GID e hash do arquivo regular de origem, mesmo
quando o caminho original foi posteriormente removido ou substituído. A camada e
o alvo do hardlink descrevem a própria entrada do link. Diretórios implícitos
não têm metadados TAR próprios e não são listados. Whiteouts e conteúdo removido
não são inventariados. Nomes de arquivos e alvos ainda podem conter informação
sensível e devem ser tratados conforme o ambiente de uso.

O escopo é limitado ao perfil suportado do leitor; formatos não suportados,
referências ambíguas, truncamentos, inconsistências e limites excedidos falham.
O chamador aloca os bytes antes da validação. Este inventário não valida
independentemente o manifesto, não faz reconciliação de pacotes nem demonstra
completude ou autenticidade. A cobertura permanece `COVERAGE_PARTIAL`.
