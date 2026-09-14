"""
Schema base com suporte a camelCase.

Todos os schemas de leitura (Read) herdam de CamelCaseSchema para
serializar automaticamente snake_case → camelCase, garantindo
compatibilidade com o frontend TypeScript do Lovable.

Exemplo:
  Backend: practice_area, column_id, is_blocked, created_at
  Frontend: practiceArea, columnId, isBlocked, createdAt

Os schemas de escrita (Create/Update) mantêm snake_case pois o frontend
envia os dados via JSON com camelCase e o Pydantic aceita ambos quando
populate_by_name=True está ativo.
"""
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelCaseSchema(BaseModel):
    """
    Schema base que serializa campos em camelCase.
    Usado em todos os schemas de resposta (Read).

    Configuração:
    - alias_generator=to_camel: gera aliases camelCase automaticamente
    - populate_by_name=True: aceita snake_case e camelCase na entrada
    - from_attributes=True: permite criar a partir de objetos ORM
    - serialization_alias via alias_generator: o OpenAPI exporta os nomes camelCase
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,   # aceita snake_case e camelCase na entrada
        from_attributes=True,    # permite criar a partir de objetos ORM
        protected_namespaces=(),
        # Garante que o OpenAPI schema use os aliases camelCase como nomes de campo
        # Isso faz o openapi.json refletir os nomes que o frontend realmente recebe
    )


class WriteSchema(BaseModel):
    """
    Schema base para criação/atualização.
    Aceita tanto snake_case quanto camelCase na entrada.
    """
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        protected_namespaces=(),
    )
