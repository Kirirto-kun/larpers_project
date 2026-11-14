from ..artifacts.storage import read_selected
from ..errors import Problem
from .validation import manifest, owned_input


def inspect(db, app, user, request):
    project_digest, input_manifest_digest = manifest(db, user, request)
    if request.selected_files and not (request.project_artifact_id and request.share_with_llm):
        raise Problem(
            "SHARING_REQUIRED", "Select a project and explicitly approve sharing selected files", 422
        )
    snippets = {}
    if request.selected_files:
        project = owned_input(db, user, request.project_artifact_id)
        snippets = read_selected(app.storage.blob(project.sha256), request.selected_files)
    return {
        "project_digest": project_digest,
        "input_manifest_digest": input_manifest_digest,
        "inputs": [binding.model_dump(mode="json") for binding in request.inputs],
        "project_artifact_id": str(request.project_artifact_id) if request.project_artifact_id else None,
        "selected_files": snippets,
        "recipe_id": request.recipe_id,
        "accelerator": request.accelerator,
    }
