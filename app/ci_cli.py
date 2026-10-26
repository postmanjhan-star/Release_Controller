"""Server-only CI policy/token administration: python -m app.ci_cli --help."""

import argparse
import json
from datetime import timedelta

from app.core.config import get_settings
from app.db.models.ci import CIPolicy, CIServiceToken
from app.db.session import SessionLocal
from app.domain.shared.time import utc_now
from app.services.ci_deployment_service import CIError, policy_for, resolve_component
from app.services.ci_tokens import create_token


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    policy = sub.add_parser("set-policy", help="Configure stages for an existing component/target")
    create = sub.add_parser("create-token", help="Show a new scoped secret exactly once")
    for command in (policy, create):
        command.add_argument("--project", required=True)
        command.add_argument("--component", required=True)
        command.add_argument("--target", required=True)
        command.add_argument("--branch", required=True)
    policy.add_argument("--source-stage", action="append", required=True)
    policy.add_argument("--promotion-stage", action="append", required=True)
    create.add_argument("--name", required=True)
    create.add_argument(
        "--expires-in-days", type=int, default=90, choices=range(1, 366), metavar="1..365"
    )
    revoke = sub.add_parser("revoke-token", help="Revoke by identifier, never by secret")
    revoke.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        try:
            if args.command == "revoke-token":
                token = db.get(CIServiceToken, args.id)
                if token is None:
                    raise CIError("CI_TOKEN_NOT_FOUND", 404)
                token.revoked_at = token.revoked_at or utc_now()
                db.commit()
                print(json.dumps({"id": token.id, "revoked": True}))
                return
            component = resolve_component(db, args.project, args.component)
            if (
                component.promote_target_override
                and component.promote_target_override != args.target
            ):
                raise CIError("TARGET_CONFIGURATION_MISMATCH")
            if not args.branch.strip() or not args.target.strip():
                raise CIError("EMPTY_SCOPE")
            if args.command == "set-policy":
                names = [*args.source_stage, *args.promotion_stage]
                if any(not name.strip() or len(name) > 100 for name in names):
                    raise CIError("INVALID_STAGE_NAME")
                try:
                    row = policy_for(db, component.id, args.target)
                except CIError:
                    row = CIPolicy(component_id=component.id, target=args.target)
                    db.add(row)
                row.branch = args.branch
                row.source_stages = list(dict.fromkeys(args.source_stage))
                row.promotion_stages = list(dict.fromkeys(args.promotion_stage))
                db.commit()
                print(json.dumps({"policy_id": row.id}))
                return
            policy = policy_for(db, component.id, args.target)
            if policy.branch != args.branch or not args.name.strip() or len(args.name) > 100:
                raise CIError("TOKEN_SCOPE_OR_NAME_INVALID")
            token, raw = create_token(
                db,
                name=args.name,
                project_id=component.project_id,
                component_id=component.id,
                target=args.target,
                branch=args.branch,
                expires_at=utc_now() + timedelta(days=args.expires_in_days),
            )
            print(
                json.dumps(
                    {"id": token.id, "token": raw, "expires_at": token.expires_at.isoformat()}
                )
            )
        except CIError as exc:
            parser.exit(1, f"{exc.code}\n")
        except Exception:
            # DB drivers/transport errors may include credentials or query values.
            db.rollback()
            parser.exit(1, "CI administration failed; check database and configuration\n")


if __name__ == "__main__":
    get_settings()
    main()
