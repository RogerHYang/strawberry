"""GraphQLHTTPHandler

A consumer to provide a graphql endpoint, and optionally graphiql.
"""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Mapping, Optional

from channels.db import database_sync_to_async
from channels.generic.http import AsyncHttpConsumer
from strawberry.channels.context import StrawberryChannelsContext
from strawberry.http import (
    process_result,
)
from strawberry.http.async_base_view import AsyncBaseHTTPView, AsyncHTTPRequestAdapter
from strawberry.http.exceptions import HTTPException
from strawberry.http.types import FormData, HTTPMethod, QueryParams
from strawberry.http.typevars import Context, RootValue
from strawberry.types.graphql import OperationType
from strawberry.utils.graphiql import get_graphiql_html

from .base import ChannelsConsumer

if TYPE_CHECKING:
    from strawberry.http import GraphQLHTTPResponse, GraphQLRequestData
    from strawberry.schema import BaseSchema
    from strawberry.types import ExecutionResult


class MethodNotAllowed(Exception):
    ...


class ExecutionError(Exception):
    ...


@dataclasses.dataclass
class Result:
    response: bytes
    status: int = 200
    content_type: str = "application/json"


class ChannelsRequestAdapter(AsyncHTTPRequestAdapter):
    def __init__(self, request: ChannelsConsumer):
        self.request = request

    @property
    def query_params(self) -> QueryParams:
        return {}

    @property
    def method(self) -> HTTPMethod:
        return self.request["method"].upper()

    @property
    def headers(self) -> Mapping[str, str]:
        return {}

    @property
    def content_type(self) -> Optional[str]:
        return None

    async def get_body(self) -> bytes:
        ...

    async def get_form_data(self) -> FormData:
        ...


class GraphQLHTTPConsumer(
    AsyncBaseHTTPView[
        ChannelsConsumer,
        Any,
        Any,
        Context,
        RootValue,
    ],
    ChannelsConsumer,
    AsyncHttpConsumer,
):
    """A consumer to provide a view for GraphQL over HTTP.

    To use this, place it in your ProtocolTypeRouter for your channels project:

    ```
    from strawberry.channels import GraphQLHttpRouter
    from channels.routing import ProtocolTypeRouter
    from django.core.asgi import get_asgi_application

    application = ProtocolTypeRouter({
        "http": URLRouter([
            re_path("^graphql", GraphQLHTTPRouter(schema=schema)),
            re_path("^", get_asgi_application()),
        ]),
        "websocket": URLRouter([
            re_path("^ws/graphql", GraphQLWebSocketRouter(schema=schema)),
        ]),
    })
    ```
    """

    allow_queries_via_get: bool = True
    request_adapter_class = ChannelsRequestAdapter

    def __init__(
        self,
        schema: BaseSchema,
        graphiql: bool = True,
        allow_queries_via_get: bool = True,
        subscriptions_enabled: bool = True,
        **kwargs: Any,
    ):
        self.schema = schema
        self.graphiql = graphiql
        self.allow_queries_via_get = allow_queries_via_get
        self.subscriptions_enabled = subscriptions_enabled
        super().__init__(**kwargs)

    async def handle(self, body: bytes) -> None:
        try:
            response = await self.run(self.scope)
            await self.send_response(
                405,
                b"Method not allowed",
                headers=[(b"Allow", b"GET, POST")],
            )
        except HTTPException as e:
            await self.send_response(e.status_code, e.reason.encode())

    def create_response(
        self, response_data: GraphQLHTTPResponse, sub_response: Any
    ) -> Any:
        return None

    async def get_root_value(self, request: ChannelsConsumer) -> RootValue:
        return None

    async def get_context(self, request: ChannelsConsumer, response: Any) -> Context:
        return {
            "request": request,
            "response": response,
        }

    async def get_sub_response(self, request: ChannelsConsumer) -> Any:
        return {}

    async def parse_multipart_body(self, body: bytes) -> GraphQLRequestData:
        raise ExecutionError("Unable to parse the multipart body")

    async def execute(self, request_data: GraphQLRequestData) -> GraphQLHTTPResponse:
        context = await self.get_context()
        root_value = await self.get_root_value()

        method = self.scope["method"]
        allowed_operation_types = OperationType.from_http(method)
        if not self.allow_queries_via_get and method == "GET":
            allowed_operation_types = allowed_operation_types - {OperationType.QUERY}

        result = await self.schema.execute(
            query=request_data.query,
            root_value=root_value,
            variable_values=request_data.variables,
            context_value=context,
            operation_name=request_data.operation_name,
            allowed_operation_types=allowed_operation_types,
        )
        return await self.process_result(result)

    async def render_graphiql(self, body) -> Result:
        html = get_graphiql_html(self.subscriptions_enabled)
        return Result(response=html.encode(), content_type="text/html")


class SyncGraphQLHTTPConsumer(GraphQLHTTPConsumer):
    """Synchronous version of the HTTPConsumer.

    This is the same as `GraphQLHTTPConsumer`, but it can be used with
    synchronous schemas (i.e. the schema's resolvers are espected to be
    synchronous and not asynchronous).
    """

    def get_root_value(self, request: Optional[ChannelsConsumer] = None) -> Any:
        return None

    def get_context(  # type: ignore[override]
        self,
        request: Optional[ChannelsConsumer] = None,
    ) -> StrawberryChannelsContext:
        return StrawberryChannelsContext(request=request or self)

    def process_result(  # type:ignore [override]
        self, result: ExecutionResult
    ) -> GraphQLHTTPResponse:
        return process_result(result)

    # Sync channels is actually async, but it uses database_sync_to_async to call
    # handlers in a threadpool. Check SyncConsumer's documentation for more info:
    # https://github.com/django/channels/blob/main/channels/consumer.py#L104
    @database_sync_to_async
    def execute(self, request_data: GraphQLRequestData) -> GraphQLHTTPResponse:
        context = self.get_context(self)
        root_value = self.get_root_value(self)

        method = self.scope["method"]
        allowed_operation_types = OperationType.from_http(method)
        if not self.allow_queries_via_get and method == "GET":
            allowed_operation_types = allowed_operation_types - {OperationType.QUERY}

        result = self.schema.execute_sync(
            query=request_data.query,
            root_value=root_value,
            variable_values=request_data.variables,
            context_value=context,
            operation_name=request_data.operation_name,
            allowed_operation_types=allowed_operation_types,
        )
        return self.process_result(result)
