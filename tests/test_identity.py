import unittest

from report_agent.identity import AuthenticationError, OidcReviewer


class OidcReviewerTests(unittest.TestCase):
    def test_accepts_verified_approver_claims(self):
        identity = OidcReviewer("issuer", "report-agent", "https://example.test/jwks",
                                decoder=lambda token: {"sub": "alice", "roles": ["report_approver"],
                                                       "policy_version": "p2"}).resolve("signed-token")
        self.assertEqual(identity.subject, "alice")
        self.assertEqual(identity.policy_version, "p2")

    def test_rejects_user_without_approval_role(self):
        verifier = OidcReviewer("issuer", "report-agent", "https://example.test/jwks",
                                decoder=lambda token: {"sub": "bob", "roles": ["analyst"],
                                                       "policy_version": "p2"})
        with self.assertRaisesRegex(AuthenticationError, "审批权限"):
            verifier.resolve("signed-token")

    def test_rejects_invalid_token_without_disclosing_it(self):
        def fail(_token):
            raise ValueError("sensitive-token")

        verifier = OidcReviewer("issuer", "report-agent", "https://example.test/jwks", decoder=fail)
        with self.assertRaises(AuthenticationError) as caught:
            verifier.resolve("sensitive-token")
        self.assertNotIn("sensitive-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
