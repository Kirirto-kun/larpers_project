// Package benchmark measures prepared local work in the same protected Docker
// runtime as distributed attempts, without a scheduler or artifact transport.
package benchmark

import (
	"campus/runner/internal/client"
	"campus/runner/internal/session"
	"campus/runner/internal/telemetry"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
)

type OriginSignature struct {
	Algorithm    string `json:"algorithm"`
	CredentialID string `json:"credential_id"`
	Scope        string `json:"scope"`
	Value        string `json:"value"`
}

// Local schema1 records predate the API Artifact DTO. Their bytes are signed,
// so keep this descriptor independent from transport naming/version changes.
type LocalOutput struct {
	ArtifactID string `json:"artifact_id"`
	Digest     string `json:"digest"`
	SizeBytes  int64  `json:"size_bytes"`
	Path       string `json:"path,omitempty"`
	Kind       string `json:"kind,omitempty"`
}
type Measurement struct {
	Repetition           int                `json:"repetition"`
	ColdWarm             string             `json:"cold_warm"`
	StartedAt            string             `json:"started_at"`
	FinishedAt           string             `json:"finished_at"`
	DurationMS           float64            `json:"duration_ms"`
	ValidationDurationMS float64            `json:"validation_duration_ms"`
	OverheadDurationMS   float64            `json:"overhead_duration_ms"`
	Quality              string             `json:"quality"`
	BenchmarkEligible    bool               `json:"benchmark_eligible"`
	Units                int64              `json:"units"`
	Validation           map[string]any     `json:"validation,omitempty"`
	Utilization          []telemetry.Sample `json:"utilization_samples"`
	Outputs              []LocalOutput      `json:"outputs"`
}
type StandaloneRecord struct {
	SchemaVersion        string                `json:"schema_version"`
	DeviceID             string                `json:"device_id"`
	InstallationID       string                `json:"installation_id"`
	ConfigDigest         string                `json:"config_digest"`
	ManifestDigest       string                `json:"manifest_digest"`
	ModelDigest          string                `json:"model_digest,omitempty"`
	ImageDigest          string                `json:"image_digest"`
	Adapter              client.Adapter        `json:"adapter"`
	AdapterDigest        string                `json:"adapter_digest"`
	ResourceLimits       client.ResourceLimits `json:"resource_limits"`
	Batch                int                   `json:"batch"`
	Concurrency          int                   `json:"concurrency"`
	TimingScope          string                `json:"timing_scope"`
	CachePolicy          string                `json:"cache_policy"`
	Origin               string                `json:"origin"`
	ProvenanceDurationMS float64               `json:"provenance_duration_ms"`
	Provenance           map[string]any        `json:"provenance,omitempty"`
	Runs                 []Measurement         `json:"runs"`
	Signature            *OriginSignature      `json:"signature,omitempty"`
}

func sign(record *StandaloneRecord, identity session.Identity) error {
	record.Signature = nil
	if identity.Credential == "" || !client.IDPattern.MatchString(identity.CredentialID) || !client.IDPattern.MatchString(identity.NodeID) {
		record.Origin = "unsigned_local"
		return nil
	}
	record.Origin = "registered_credential_hmac_origin_only"
	record.Signature = &OriginSignature{Algorithm: "HMAC-SHA256", CredentialID: identity.CredentialID, Scope: "origin_only_not_correctness", Value: ""}
	raw, err := json.Marshal(record)
	if err != nil {
		return err
	}
	mac := hmac.New(sha256.New, []byte(identity.Credential))
	mac.Write(raw)
	record.Signature.Value = hex.EncodeToString(mac.Sum(nil))
	return nil
}

// VerifyOrigin checks integrity with the registered credential. It does not
// establish trusted measurement or correctness; only the real validator can
// make the record benchmark eligible, subject to the coordinator's policy.
func VerifyOrigin(record StandaloneRecord, credential string) bool {
	signature := record.Signature
	if signature == nil || signature.Algorithm != "HMAC-SHA256" || signature.Scope != "origin_only_not_correctness" || record.Origin != "registered_credential_hmac_origin_only" || credential == "" {
		return false
	}
	metadata := *signature
	metadata.Value = ""
	record.Signature = &metadata
	raw, err := json.Marshal(record)
	if err != nil {
		return false
	}
	actual, err := hex.DecodeString(signature.Value)
	if err != nil {
		return false
	}
	mac := hmac.New(sha256.New, []byte(credential))
	mac.Write(raw)
	return hmac.Equal(actual, mac.Sum(nil))
}
