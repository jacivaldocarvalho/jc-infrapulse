# F3C.4.2B — reconstrução virtual OCI

`reconstruct_rootfs` aplica camadas USTAR não comprimidas validadas em memória,
sem extração ou resolução de symlinks no sistema operacional. O chamador deve
validar independentemente o manifesto, os descritores e a ordem das camadas;
os limites e verificações de digest do leitor continuam obrigatórios.

Whiteouts comuns e diretórios opacos removem apenas entradas de camadas inferiores,
antes de processar as entradas da camada corrente, independentemente da ordem
TAR. Diretórios implícitos são permitidos; percorrer um arquivo ou symlink como
diretório falha. Hardlinks apontam apenas para arquivos regulares já disponíveis,
inclusive hardlinks anteriores, e preservam sua identidade após substituições.
Referências futuras, ciclos, escapes e tipos não suportados são rejeitados. A saída
é ordenada por caminho e guarda a camada e o caminho de origem do conteúdo; não
expõe conteúdo nem constitui um inventário de arquivos.

Esta etapa não prova autenticidade do manifesto ou do builder, nem completude
do SBOM. A cobertura continua `COVERAGE_PARTIAL`. Inventário, reconciliação e
integração operacional permanecem para etapas posteriores.
