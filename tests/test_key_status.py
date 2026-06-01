import unittest

from launch_ui import (
    classify_key_test_response,
    common_provider_from_items,
    initial_key_status,
    normalize_provider_name,
    remove_accounts_by_name,
)


class KeyStatusTests(unittest.TestCase):
    def test_success_status_is_available(self) -> None:
        result = classify_key_test_response(200, '{"id":"resp_123"}')

        self.assertEqual(result.status, "可用")
        self.assertIn("HTTP 200", result.detail)

    def test_promotional_credit_401_is_claude_code_only(self) -> None:
        body = (
            '{"error":"Your remaining $97.23 promotional credit can only be used in '
            'Claude Code. Please use Claude Code to spend this credit."}'
        )

        result = classify_key_test_response(401, body)

        self.assertEqual(result.status, "仅 Claude Code 可用")
        self.assertIn("promotional credit", result.detail)

    def test_generic_401_is_unavailable(self) -> None:
        result = classify_key_test_response(401, '{"error":"invalid api key"}')

        self.assertEqual(result.status, "401 不可用")
        self.assertIn("invalid api key", result.detail)

    def test_429_reports_quota_or_rate_limit(self) -> None:
        result = classify_key_test_response(429, '{"error":"quota exceeded"}')

        self.assertEqual(result.status, "额度或限速")
        self.assertIn("quota exceeded", result.detail)


class ProviderNameTests(unittest.TestCase):
    def test_provider_name_is_ascii_safe(self) -> None:
        self.assertEqual(normalize_provider_name(" right code / dev "), "right-code-dev")

    def test_common_provider_returns_current_provider_when_consistent(self) -> None:
        provider = common_provider_from_items(
            [
                {"provider": "rightcode"},
                {"provider": "rightcode"},
            ]
        )

        self.assertEqual(provider, "rightcode")

    def test_common_provider_is_blank_when_mixed(self) -> None:
        provider = common_provider_from_items(
            [
                {"provider": "rightcode"},
                {"provider": "other"},
            ]
        )

        self.assertEqual(provider, "")


class AccountRemovalTests(unittest.TestCase):
    def test_remove_accounts_by_name_removes_only_matching_accounts(self) -> None:
        remaining, removed = remove_accounts_by_name(
            [
                {"name": "keep-1"},
                {"name": "delete-1"},
                {"name": "keep-2"},
            ],
            {"delete-1"},
        )

        self.assertEqual(removed, 1)
        self.assertEqual([item["name"] for item in remaining], ["keep-1", "keep-2"])

    def test_remove_accounts_by_name_keeps_non_account_items(self) -> None:
        remaining, removed = remove_accounts_by_name(
            [
                {"name": "delete-1"},
                "not-an-account",
            ],
            {"delete-1"},
        )

        self.assertEqual(removed, 1)
        self.assertEqual(remaining, ["not-an-account"])


class KeyStatusPersistenceTests(unittest.TestCase):
    def test_existing_status_is_preserved_after_refresh(self) -> None:
        status = initial_key_status("api-freemodel-dev-1", False, {"api-freemodel-dev-1": "可用"})

        self.assertEqual(status, "可用")

    def test_missing_status_uses_preferred_default(self) -> None:
        status = initial_key_status("api-freemodel-dev-1", True, {})

        self.assertEqual(status, "preferred / 未测试")


if __name__ == "__main__":
    unittest.main()