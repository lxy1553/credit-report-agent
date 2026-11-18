from dataclasses import dataclass


class AuthenticationError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewerIdentity:
    subject: str
    policy_version: str
    roles: frozenset[str] = frozenset()
    domains: frozenset[str] = frozenset()


class OidcReviewer:
    """Verify OIDC JWTs and authorize report users or approvers from claims."""

    def __init__(self, issuer: str, audience: str, jwks_url: str, decoder=None):
        if not all((issuer, audience, jwks_url)):
            raise ValueError("OIDC issuer、audience 和 JWKS URL 必填")
        if decoder is not None:
            self._decode = decoder
            return
        try:
            import jwt
        except ImportError as exc:
            raise RuntimeError("审批身份验证需要安装 PyJWT[crypto]") from exc
        jwks = jwt.PyJWKClient(jwks_url, cache_keys=True)

        def decode(token):
            key = jwks.get_signing_key_from_jwt(token)
            return jwt.decode(token, key.key, algorithms=["RS256"], issuer=issuer,
                              audience=audience, options={"require": ["exp", "iat", "sub"]})

        self._decode = decode

    def resolve(self, token: str, required_role: str = "report_approver",
                any_roles: set[str] | None = None, required_domain: str | None = None) -> ReviewerIdentity:
        if not isinstance(token, str) or not token or len(token) > 16_384:
            raise AuthenticationError("无效的身份令牌")
        try:
            claims = self._decode(token)
        except Exception:
            raise AuthenticationError("身份令牌验证失败") from None
        subject = claims.get("sub")
        policy = claims.get("policy_version")
        roles = claims.get("roles")
        if not isinstance(subject, str) or not subject or len(subject) > 128:
            raise AuthenticationError("无效的审批人身份")
        if not isinstance(policy, str) or not policy or len(policy) > 128:
            raise AuthenticationError("无效的权限策略版本")
        domains = claims.get("domains", [])
        if (not isinstance(roles, list) or any(not isinstance(role, str) for role in roles)
                or not isinstance(domains, list) or any(not isinstance(domain, str) for domain in domains)):
            raise AuthenticationError("无效的身份权限声明")
        role_set, domain_set = frozenset(roles), frozenset(domains)
        if required_role not in role_set:
            message = "当前身份没有报告审批权限" if required_role == "report_approver" else "当前身份没有报告使用权限"
            raise AuthenticationError(message)
        if any_roles and not (role_set & any_roles):
            raise AuthenticationError("当前身份没有信贷指标读取权限")
        if required_domain and required_domain not in domain_set:
            raise AuthenticationError("当前身份没有信贷数据域权限")
        return ReviewerIdentity(subject, policy, role_set, domain_set)
