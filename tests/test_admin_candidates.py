from types import SimpleNamespace

from shrink.adapters.admin_events import admin_candidates


def test_admin_choices_expose_accounts_without_credentials():
    context = SimpleNamespace(
        get_config=lambda: {"admins_id": ["owner", "guest", "owner"], "secret": "not-visible"},
        platform_manager=SimpleNamespace(get_insts=lambda: [
            SimpleNamespace(meta=lambda: SimpleNamespace(id="fixture"))
        ]),
    )
    result = admin_candidates(context)
    assert result["administrators"] == [
        {"id": "owner", "label": "管理员 owner"},
        {"id": "guest", "label": "管理员 guest"},
    ]
    assert result["items"][0]["account"] == "owner"
    assert "secret" not in str(result)
    context.platform_manager.get_insts = lambda: []
    result = admin_candidates(context)
    assert result["items"] == [] and result["reason"]
    assert len(result["administrators"]) == 2
