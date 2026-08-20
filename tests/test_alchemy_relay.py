import pytest
from unittest.mock import AsyncMock, MagicMock
from alchemy_relay import AlchemyRelay


@pytest.mark.asyncio
async def test_send_bundle_success():
    relay = AlchemyRelay(alchemy_api_key="test_key")

    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(return_value={"result": "0x123"})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.closed = False
    relay._session = mock_session

    result = await relay.send_bundle(
        signed_txs=["0xabc"],
        block_number=100,
        signer_address="0xsigner",
        signature="0xsig",
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_send_bundle_flashbots_error():
    relay = AlchemyRelay()

    mock_resp = AsyncMock()
    mock_resp.json = AsyncMock(return_value={"error": {"message": "failed"}})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.closed = False
    relay._session = mock_session

    result = await relay.send_bundle(
        signed_txs=["0xabc"],
        block_number=100,
        signer_address="0xsigner",
        signature="0xsig",
    )
    assert result["success"] is False


@pytest.mark.asyncio
async def test_send_private_tx_no_key():
    relay = AlchemyRelay(alchemy_api_key="")
    result = await relay.send_private_tx("0xabc")
    assert result["success"] is False
    assert "No Alchemy API key" in result["error"]
