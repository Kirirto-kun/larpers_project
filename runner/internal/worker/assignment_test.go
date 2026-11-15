package worker

import (
	"campus/runner/internal/client"
	"context"
	"strings"
	"testing"
)

func TestAssignmentRejectsFloatingImageAndNetworkBeforeRuntime(t *testing.T) {
	a := client.Assignment{ImageDigest: "python:latest", NetworkPolicy: "none"}
	r := Runner{AllowedImages: map[string]string{"python:latest": "linux/amd64"}}
	if _, e := r.RunAssignment(context.Background(), a); e == nil {
		t.Fatal("floating image accepted")
	}
	a.ImageDigest = "demo@sha256:" + strings.Repeat("a", 64)
	a.NetworkPolicy = "host"
	if _, e := r.RunAssignment(context.Background(), a); e == nil {
		t.Fatal("network escalation accepted")
	}
}
