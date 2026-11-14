# Generated contract DTOs

Source: `origin/codex/control-plane` commit
`5a5bd297dfc0c9fd704e408a929d56643042c4ba`.
`contracts/openapi-v1.yaml` SHA-256:
`f6811226468f3dcba58e734654044b4710687b0e942f849430ee8b2411b1ace8`.

Generated using that commit's unmodified `contracts/generate.py --target go
--out <runner>/internal/generated/api.go`, then Go 1.25.6 gofmt. The generator
consumes the committed OpenAPI without importing the absent production server.
The schema/fixture check from the same commit passes in its locked Python3.12
environment. This records DTO provenance, not a successful production API test.

Rich local execution policy remains in client.Assignment/ResourceLimits. Its
extra fields are not silently sent as strict foundation request DTOs.

`../client/testdata/foundation-assignment.json` is a complete locally constructed
fixture validated with the pinned foundation's actual Pydantic Assignment DTO.
It proves the safety gap occurs for a schema-valid assignment; it is not a
recorded server scheduling response.
