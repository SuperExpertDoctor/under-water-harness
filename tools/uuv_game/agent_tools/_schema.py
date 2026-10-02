"""Shared JSON-schema fragments for the builtin tool parameters."""

SNAPSHOT = {
    "episode_id": {"type": "string", "minLength": 1},
    "mission_revision": {"type": "integer", "minimum": 0},
}

FLEET = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
    "minItems": 1, "maxItems": 8, "uniqueItems": True,
}

BBOX = {
    "type": "array",
    "items": {"type": "number", "minimum": 0, "maximum": 4000},
    "minItems": 4, "maxItems": 4,
    "description": "Only for an explicitly scoped search with explicit "
                   "members. Omit bbox for automatic whole-area search. "
                   "[xmin,ymin,xmax,ymax] in meters, not UI cells.",
}


def algorithm(algorithm_id):
    return {"type": "string", "enum": ["default", algorithm_id]}


def obj(properties, required=()):
    schema = {"type": "object", "properties": properties,
              "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return schema
