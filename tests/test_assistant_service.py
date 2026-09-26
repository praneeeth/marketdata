"""The assistant module owns conversation use cases, not HTTP route helpers."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from pan_agent import ModelMessage, RunRequest, ToolRisk, ToolSpec

from src.platform.persistence.database import Base
from src.platform.persistence.models import ChatConversation, ChatMessage  # noqa: F401 - registers metadata


def test_assistant_service_creates_reads_and_deletes_legacy_chat_history():
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.schemas import CreateConversationCommand
    from src.modules.assistant.service import AssistantService

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    service = AssistantService(AssistantRepository(session))

    created = service.create_conversation(
        CreateConversationCommand(stock_symbol="INFY", stock_market="IN", initial_context="from the stock page")
    )
    service.record_user_message(created.id, "take a look for me")

    detail = service.get_conversation(created.id)
    assert detail.conversation.stock_symbol == "INFY"
    assert detail.messages[0].content == "take a look for me"

    service.delete_conversation(created.id)
    assert service.list_conversations() == []
    session.close()


def test_service_policy_hides_tools_outside_an_action_allowlist():
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.service import AssistantService

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    policy = AssistantService(AssistantRepository(session)).build_tool_policy()
    write_tool = ToolSpec(
        name="create_alert",
        title="Create alert",
        description="write",
        risk=ToolRisk.WRITE,
    )
    read_tool = ToolSpec(
        name="get_portfolio",
        title="Get holdings",
        description="read",
        risk=ToolRisk.READ,
    )
    request = RunRequest(
        run_id="action",
        messages=[ModelMessage(role="user", content="create an alert")],
        context={"allowed_tool_names": ["create_alert"]},
    )

    assert policy.is_tool_visible(request, write_tool) is True
    assert policy.is_tool_visible(request, read_tool) is False
    session.close()
    engine.dispose()
