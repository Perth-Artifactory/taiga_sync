import pytest

from slack import misc


def test_base_convert_markdown():
    text = """# Heading

This is a paragraph.

* List item 1
* List item 2
This is **bold** and this is _italic_. This is also *italic*.
This is `code`
> This is a quote
> This is the second line

This is a [link](https://example.com)
This is<br>over two lines
"""

    expected_output = """*Heading*
This is a paragraph.
• List item 1
• List item 2
This is *bold* and this is _italic_. This is also _italic_.
This is `code`
> This is a quote
> This is the second line
This is a <https://example.com|link>
This is
over two lines"""

    assert misc.convert_markdown(text) == expected_output


def test_validate_valid_blocks(mocker):
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Hello"}}]
    assert misc.validate(blocks) == True


def test_validate_invalid_blocks(mocker):
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": ""}}]
    assert misc.validate(blocks) == False


def test_validate_invalid_message_blocks(mocker):
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Hello"}}]
    blocks = blocks * 51
    assert misc.validate(blocks, surface="message") == False
    assert misc.validate(blocks, surface="msg") == False


def test_validate_invalid_modal_blocks(mocker):
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Hello"}}]
    blocks = blocks * 101
    assert misc.validate(blocks, surface="modal") == False
    assert misc.validate(blocks, surface="home") == False


def test_validate_invalid_surface():
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Hello"}}]
    with pytest.raises(ValueError, match="Invalid surface type: dialog"):
        misc.validate(blocks, surface="dialog")


def test_name_mapper_single_user(mocker):
    slack_app = mocker.Mock()
    slack_app.client.users_info.return_value = {"user": {"real_name": "John Doe"}}
    assert misc.name_mapper("U12345", slack_app) == "John Doe"


def test_name_mapper_multiple_users(mocker):
    slack_app = mocker.Mock()
    slack_app.client.users_info.side_effect = [
        {"user": {"real_name": "John Doe"}},
        {"user": {"real_name": "Jane Smith"}},
    ]
    assert misc.name_mapper("U12345,U67890", slack_app) == "John Doe, Jane Smith"


def test_name_mapper_edge_cases(mocker):
    slack_app = mocker.Mock()
    slack_app.client.users_info.side_effect = []
    assert misc.name_mapper("Unknown", slack_app) == "Unknown"
    assert misc.name_mapper("No one", slack_app) == "No one"
    assert misc.name_mapper("", slack_app) == ""


def test_name_mapper_display_name(mocker):
    slack_app = mocker.Mock()
    slack_app.client.users_info.return_value = {
        "user": {"real_name": None, "profile": {"display_name": "John"}}
    }
    assert misc.name_mapper("U12345", slack_app) == "John"


def test_send_dm_success(mocker):
    slack_app = mocker.Mock()
    slack_app.client.conversations_open.return_value = {"channel": {"id": "C12345"}}
    slack_app.client.chat_postMessage.return_value = {"ok": True}
    assert misc.send_dm("U12345", "Hello", slack_app) == True


def test_send_dm_failure(mocker):
    slack_app = mocker.Mock()
    slack_app.client.conversations_open.return_value = {"channel": {"id": "C12345"}}
    slack_app.client.chat_postMessage.return_value = {"ok": False}
    assert misc.send_dm("U12345", "Hello", slack_app) == False


def test_map_recipients_user():
    recipients = ["U12345"]
    tidyhq_cache = {}
    config = {}
    expected_output = {"user": ["U12345"], "channel": []}
    assert misc.map_recipients(recipients, tidyhq_cache, config) == expected_output


def test_map_recipients_channel():
    recipients = ["C12345"]
    tidyhq_cache = {}
    config = {}
    expected_output = {"user": [], "channel": ["C12345"]}
    assert misc.map_recipients(recipients, tidyhq_cache, config) == expected_output
