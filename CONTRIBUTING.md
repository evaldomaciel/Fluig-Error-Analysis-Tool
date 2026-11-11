# Como contribuir

Obrigado por ajudar a melhorar o Fluig Error Analysis Tool! As contribuições são feitas por pull request
e precisam ser aprovadas por `@evaldomaciel` antes de entrar na branch principal.

## Preparar o ambiente

1. Faça um fork do repositório e clone o seu fork.
2. Crie uma branch a partir de `main` para a sua alteração.
3. Use Python 3.8 ou superior. Para gerar relatórios PDF durante o desenvolvimento, instale também
   `reportlab` com `pip install -e ".[pdf]"`.

## Enviar uma alteração

1. Mantenha cada pull request focado em uma mudança e explique o problema ou a motivação.
2. Inclua ou atualize testes para comportamentos alterados.
3. Execute `python -m unittest -v` e confirme que os testes passam.
4. Abra um pull request direcionado à branch `main`, preencha o modelo e aguarde a revisão.
5. Faça os ajustes solicitados na mesma branch. A alteração só será integrada após aprovação do
   CODEOWNER e validação dos testes automatizados.

Não inclua logs reais, relatórios gerados, credenciais ou dados de clientes. O `.gitignore` já exclui
arquivos de log e relatórios comuns; confira os arquivos do pull request antes de enviá-lo.

## Estilo e escopo

- Preserve a compatibilidade com Python 3.8 ou superior.
- Prefira mudanças pequenas, consistentes com a estrutura atual e cobertas por testes.
- Para mudanças na análise, inclua exemplos seguros e anonimizados quando necessário.
