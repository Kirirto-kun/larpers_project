package benchmark

import (
	"bytes"
	"campus/runner/internal/client"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"testing"
)

func TestSchemaOneSignedOutputsRemainVerifiableAfterAPIArtifactChange(t *testing.T) {
	r := StandaloneRecord{SchemaVersion: "1", Origin: "registered_credential_hmac_origin_only", Runs: []Measurement{{}}, Signature: &OriginSignature{Algorithm: "HMAC-SHA256", CredentialID: "credential", Scope: "origin_only_not_correctness"}}
	raw, _ := json.Marshal(r)
	// This is the exact previous local schema1 descriptor/order, not an API DTO.
	raw = bytes.Replace(raw, []byte(`"outputs":null`), []byte(`"outputs":[{"artifact_id":"artifact","digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","size_bytes":9,"path":"workspace/model.pt","kind":"local_output"}]`), 1)
	mac := hmac.New(sha256.New, []byte("secret"))
	mac.Write(raw)
	raw = bytes.Replace(raw, []byte(`"value":""`), []byte(`"value":"`+hex.EncodeToString(mac.Sum(nil))+`"`), 1)
	var loaded StandaloneRecord
	if err := json.Unmarshal(raw, &loaded); err != nil {
		t.Fatal(err)
	}
	if !VerifyOrigin(loaded, "secret") {
		t.Fatal("published local schema1 output fields/signature were lost")
	}
}

func TestLocalInputRetainsArchiveBindingAcrossManifestPublication(t *testing.T) {
	input := LocalInput{Artifact: client.Artifact{ArtifactID: "dataset", Digest: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", SizeBytes: 9, Path: "dataset.zip", Kind: "dataset"}, Source: "data.zip"}
	raw, err := json.Marshal(input)
	if err != nil {
		t.Fatal(err)
	}
	var loaded LocalInput
	if err = json.Unmarshal(raw, &loaded); err != nil {
		t.Fatal(err)
	}
	if loaded.Kind != "dataset" || loaded.Path != "dataset.zip" || loaded.Source != "data.zip" || loaded.Digest != input.Digest {
		t.Fatalf("local archive became an opaque file after JSON publication: %s %+v", raw, loaded)
	}
}
