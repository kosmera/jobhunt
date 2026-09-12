from django.contrib import admin

from jobhunt_ai.models import (
    AgentRun,
    CandidateProfile,
    GeneratedCV,
    MatchReport,
    OfferLead,
    ScoutTarget,
)


@admin.register(CandidateProfile)
class CandidateProfileAdmin(admin.ModelAdmin):
    list_display = ["label", "owner", "full_name", "language", "is_primary", "updated_at"]
    list_filter = ["owner", "is_primary", "language"]
    search_fields = ["label", "full_name"]


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ["kind", "status", "owner", "application", "model_id",
                    "input_tokens", "output_tokens", "created_at"]
    list_filter = ["owner", "kind", "status"]
    readonly_fields = ["status", "task_id", "deadline_at", "phase", "progress_current",
                       "progress_total", "created_at", "started_at", "finished_at",
                       "fanout_started_at", "fanout_context"]
    list_select_related = ["owner", "application"]


@admin.register(ScoutTarget)
class ScoutTargetAdmin(admin.ModelAdmin):
    list_display = ["id", "run", "status", "created_leads", "deadline_at"]
    list_filter = ["status"]
    list_select_related = ["run"]
    readonly_fields = [field.name for field in ScoutTarget._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(MatchReport)
class MatchReportAdmin(admin.ModelAdmin):
    list_display = ["application", "score", "model_id", "created_at"]
    list_filter = ["score"]


@admin.register(OfferLead)
class OfferLeadAdmin(admin.ModelAdmin):
    list_display = ["title", "company_name", "owner", "location", "score", "score_confidence",
                    "status", "source_name"]
    list_filter = ["owner", "status", "source_name"]
    search_fields = ["title", "company_name"]


@admin.register(GeneratedCV)
class GeneratedCVAdmin(admin.ModelAdmin):
    list_display = ["application", "language", "model_id", "created_at"]
