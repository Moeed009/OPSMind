import typing

from app.api.deps import UserDb


def test_user_db_commits_before_response_is_sent():
    dependency = typing.get_args(UserDb)[1]
    assert dependency.scope == "function"