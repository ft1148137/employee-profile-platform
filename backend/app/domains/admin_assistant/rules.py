"""Rules and home actions reuse the manual console's domain services."""

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, JsonValue, RootModel
from sqlmodel import Session, select
from sqlmodel._compat import SQLModelConfig

from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.capability.publish import save_dictionary
from app.domains.capability.schemas import ActiveSchemaPublic, SaveDictionary
from app.domains.capability.service import activate_template, read_editor
from app.domains.change_request.handover import hand_over
from app.domains.drafting import settings
from app.domains.drafting.note import DraftingNoteInput, read_note, save_note
from app.domains.drafting.service import (
    active_employees,
    drafting_employee_ids,
    queue_drafting,
)
from app.domains.org.models import Organization
from app.domains.org.schemas import OrganizationUpdate
from app.domains.org.service import rename_organization
from app.domains.setup.models import SetupStatus
from app.domains.setup.wizard import update_step

if TYPE_CHECKING:
    from app.domains.admin_assistant.registry import AdminFunction


class RuleArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TemplateArgs(RuleArgs):
    template_id: str


class DraftArgs(RuleArgs):
    scope: Literal["all", "failed"]


class StepArgs(RuleArgs):
    key: str


class OrgArgs(OrganizationUpdate):
    model_config = SQLModelConfig(extra="forbid")


def dictionary_snapshot(db: Session, p: Principal, _: Any) -> BaseModel:
    editor = read_editor(db, p)
    return RootModel(
        editor.model_dump(include={"expected_version", "definitions"})
        | {"redraft": False}
    )


def restore_dictionary(
    db: Session, p: Principal, before: dict[str, JsonValue]
) -> tuple[str, BaseModel]:
    if not before["definitions"]:
        raise DictionaryRestoreUnavailable()
    return "update_capability_dictionary", SaveDictionary.model_validate(
        before | {"expected_version": read_editor(db, p).expected_version}
    )


def restore_import_dictionary(
    db: Session, p: Principal, before: dict[str, JsonValue]
) -> tuple[str, BaseModel]:
    _, body = restore_dictionary(db, p, before)
    return "import_capability_dictionary", body


class DictionaryRestoreUnavailable(HTTPException):
    def __init__(self) -> None:
        super().__init__(409, "启用词典之前没有旧版本可恢复，请发布新的词典内容")


def apply_template(
    db: Session, p: Principal, args: TemplateArgs, *, commit: bool = True
) -> BaseModel:
    result = ActiveSchemaPublic.model_validate(
        activate_template(db, p, args.template_id)
    )
    if commit:
        db.commit()
    return result


def start_drafting(
    db: Session, p: Principal, args: DraftArgs, *, commit: bool = True
) -> BaseModel:
    count = queue_drafting(db, p.org_id, args.scope, commit=False)
    record_audit(
        db,
        org_id=p.org_id,
        actor_type=p.actor_type,
        actor_id=str(p.user_id),
        action="initial_profiles.queued",
        target_type="organization",
        target_id=str(p.org_id),
        details={"scope": args.scope, "queued": count},
    )
    if commit:
        db.commit()
    return RootModel({"queued": count})


def handover(
    db: Session, p: Principal, _: BaseModel, *, commit: bool = True
) -> BaseModel:
    return RootModel({"count": hand_over(db, p, confirm=True, commit=commit)})


def drafting_impact(
    db: Session, p: Principal, args: settings.DraftingSettingsUpdate
) -> BaseModel:
    count = sum(not e.auto_draft_opt_out for e in active_employees(db, p.org_id))
    return RootModel(
        {
            "count": count if args.enabled else 0,
            "text": f"将为 {count} 名员工起草" if args.enabled else "关闭自动起草",
        }
    )


def draft_impact(db: Session, p: Principal, args: Any) -> BaseModel:
    redraft = isinstance(args, SaveDictionary)
    if redraft and not args.redraft:
        return RootModel({"count": 0, "text": "只更新能力词典，不重新起草"})
    settings.require_auto_drafting(db, p.org_id)
    return RootModel(
        {
            "count": len(
                drafting_employee_ids(
                    db,
                    p.org_id,
                    "all" if redraft else args.scope,
                    pending_owner_only=redraft,
                )
            ),
            "text": "起草期间管理助手暂时不能用，可以先用手工页面",
        }
    )


def register_rules(functions: dict[str, AdminFunction]) -> None:
    from app.domains.admin_assistant.registry import AdminFunction, NoArgs

    entries = [
        AdminFunction(
            "update_drafting_note",
            "data_ai",
            "change",
            "normal",
            "修改企业起草补充说明（最多 2000 字）；清空传空字符串，撤销时追加旧正文",
            DraftingNoteInput,
            save_note,
            "/admin/data-ai?tab=model",
            (("PUT", "/drafting-note"),),
            snapshot=lambda d, p, a: DraftingNoteInput(
                text=read_note(d, p.org_id).text
            ),
        ),
        AdminFunction(
            "update_capability_dictionary",
            "rules",
            "change",
            "normal",
            "预览并发布能力词典；redraft 打开时重新起草尚未交付的初始画像",
            SaveDictionary,
            save_dictionary,
            "/admin/rules?tab=dictionary",
            (
                ("POST", "/capabilities/editor/preview"),
                ("PUT", "/capabilities"),
            ),
            snapshot=dictionary_snapshot,
            impact=draft_impact,
            reverse=restore_dictionary,
        ),
        AdminFunction(
            "apply_capability_template",
            "rules",
            "change",
            "normal",
            "应用能力词典模板",
            TemplateArgs,
            apply_template,
            "/admin/rules?tab=dictionary",
            (("POST", "/capability-schema"),),
            snapshot=dictionary_snapshot,
            impact=lambda d, p, a: RootModel(
                {
                    "count": len(active_employees(d, p.org_id)),
                    "text": "更新这些员工可使用的能力词典",
                }
            ),
            reverse=restore_dictionary,
        ),
        AdminFunction(
            "set_auto_drafting",
            "data_ai",
            "change",
            "normal",
            "打开或关闭企业自动起草",
            settings.DraftingSettingsUpdate,
            settings.save_settings,
            "/admin/data-ai?tab=model",
            (("PUT", "/drafting-settings"),),
            snapshot=lambda d, p, a: settings.DraftingSettingsUpdate.model_validate(
                settings.read_settings(d, p.org_id).model_dump(
                    include=set(settings.DraftingSettingsUpdate.model_fields)
                )
            ),
            impact=drafting_impact,
            merge_settings=True,
        ),
        AdminFunction(
            "start_initial_drafting",
            "home",
            "change",
            "normal",
            "为全部或失败的员工启动初始画像起草，不能撤销",
            DraftArgs,
            start_drafting,
            "/admin",
            (("POST", "/initial-profiles/draft"),),
            impact=draft_impact,
        ),
        AdminFunction(
            "hand_over_initial_profiles",
            "home",
            "change",
            "normal",
            "将超期未过目的初始画像送交经理审批，不能撤销",
            NoArgs,
            handover,
            "/admin",
            (("POST", "/initial-profiles/hand-over"),),
            impact=lambda d, p, a: RootModel(
                {"count": hand_over(d, p), "text": "将送交经理审批的初始画像"}
            ),
        ),
        AdminFunction(
            "get_initial_handover",
            "home",
            "read",
            "normal",
            "预览超期初始画像的交付人数",
            NoArgs,
            lambda d, p, a: RootModel({"count": hand_over(d, p)}),
            "/admin",
            (("GET", "/initial-profiles/hand-over"),),
        ),
        AdminFunction(
            "update_org_profile",
            "home",
            "change",
            "normal",
            "修改企业名称",
            OrgArgs,
            lambda d, p, a, **kw: rename_organization(d, p, a.name, **kw),
            "/admin/setup",
            (("PATCH", ""),),
            snapshot=lambda d, p, a: OrgArgs(
                name=d.exec(
                    select(Organization.name).where(Organization.id == p.org_id)
                ).one()
            ),
        ),
    ]
    for name, status, path in (
        ("complete_setup_step", SetupStatus.COMPLETED, "complete"),
        ("skip_setup_step", SetupStatus.SKIPPED, "skip"),
    ):

        def handler(
            d: Session,
            p: Principal,
            a: StepArgs,
            *,
            commit: bool = True,
            status: SetupStatus = status,
        ) -> BaseModel:
            return update_step(d, p, a.key, status, commit=commit)

        entries.append(
            AdminFunction(
                name,
                "home",
                "change",
                "normal",
                "完成首次设置步骤" if path == "complete" else "跳过可选的首次设置步骤",
                StepArgs,
                handler,
                "/admin/setup",
                (("POST", f"/setup/steps/{{key}}/{path}"),),
            )
        )
    functions.update((f.name, f) for f in entries)
    functions["import_capability_dictionary"] = replace(
        functions["update_capability_dictionary"],
        name="import_capability_dictionary",
        description="导入能力词典；撤销时发布上一版内容",
        endpoints=(
            ("POST", "/capabilities/preview"),
            ("POST", "/capabilities/versions"),
        ),
        reverse=restore_import_dictionary,
    )
