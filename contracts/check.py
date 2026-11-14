import json

from generate import CONTRACT, ROOT, contract, schema_documents
from jsonschema import Draft202012Validator, FormatChecker


def validate_refs(schema, document):
    if isinstance(schema, dict):
        if ref := schema.get("$ref"):
            target = document
            for token in ref.removeprefix("#/").split("/"):
                target = target[token.replace("~1", "/").replace("~0", "~")]
        for value in schema.values():
            validate_refs(value, document)
    elif isinstance(schema, list):
        for value in schema:
            validate_refs(value, document)


def main():
    api = json.loads(CONTRACT.read_text())
    if (ROOT / "server/campus/main.py").is_file():
        assert api == contract(), (
            "OpenAPI drift: regenerate with contracts/generate.py --export"
        )
    validate_refs(api, api)
    for path, content in schema_documents(api):
        assert path.read_text() == content, f"Schema drift: {path.name}"
        Draft202012Validator.check_schema(json.loads(content))
    for path in sorted((ROOT / "contracts/fixtures").glob("*.json")):
        scenario = json.loads(path.read_text())
        for reply in scenario["responses"].values():
            schema = reply.get("schema")
            if schema:
                Draft202012Validator(
                    {
                        "$ref": f"#/components/schemas/{schema}",
                        "components": api["components"],
                    },
                    format_checker=FormatChecker(),
                ).validate(reply["body"])
    print("OpenAPI, standalone JSON Schemas, and fixture responses are valid")


if __name__ == "__main__":
    main()
