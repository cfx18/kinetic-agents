"""Host deployment addresses, explicitly supplied in the common YAML."""

from .slurm import remote_path


def validate_deployment(value):
    keys = {"search_root", "container_image", "evaluation_python"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("deployment requires search_root, container_image, evaluation_python")
    for name in keys:
        if not isinstance(value[name], str):
            raise ValueError("deployment addresses must be explicit strings")
        remote_path(value[name])
    root = remote_path(value["search_root"])
    if any(remote_path(value[name]).is_relative_to(root) for name in keys - {"search_root"}):
        raise PermissionError("immutable runtime resources must be separate from search jobs")
    return dict(value)
