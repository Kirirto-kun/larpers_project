package discovery

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func TestDetectedRTX5050HasNameButDoesNotOfferCUDA(t *testing.T) {
	caps, err := DiscoverWith(context.Background(), func(_ context.Context, name string, args ...string) ([]byte, error) {
		switch name {
		case "docker":
			return []byte(`{"Version":"29.2.1","Os":"linux","Arch":"amd64"}`), nil
		case "nvidia-smi":
			return []byte("0, NVIDIA GeForce RTX 5050 Laptop GPU, 8151, 576.88\n"), nil
		}
		return nil, errors.New("unexpected inventory command")
	})
	if err != nil || len(caps.GPUs) != 1 {
		t.Fatalf("NVIDIA inventory missing: caps=%+v err=%v", caps, err)
	}
	if caps.GPUs[0].CUDAVerified || len(caps.OfferedBudget.GPUIDs) != 0 {
		t.Fatalf("detection offered unverified CUDA: %+v", caps)
	}
	raw, err := json.Marshal(caps.GPUs[0])
	if err != nil || !strings.Contains(string(raw), `"name":"NVIDIA GeForce RTX 5050 Laptop GPU"`) {
		t.Fatalf("GPU model missing from local inventory: %s %v", raw, err)
	}
}

func TestMissingNVIDIADriverIsUnknownRatherThanAbsentHardware(t *testing.T) {
	caps, err := DiscoverWith(context.Background(), func(_ context.Context, name string, args ...string) ([]byte, error) {
		if name == "docker" {
			return []byte(`{"Version":"29.2.1","Os":"linux","Arch":"amd64"}`), nil
		}
		return nil, errors.New("driver unavailable")
	})
	if err != nil || len(caps.GPUs) != 0 || len(caps.OfferedBudget.GPUIDs) != 0 {
		t.Fatalf("driver failure offered CUDA: %+v %v", caps, err)
	}
	if !strings.Contains(strings.Join(caps.Diagnostics, " "), "NVIDIA detection unavailable") {
		t.Fatalf("hardware absence was inferred from missing driver: %+v", caps.Diagnostics)
	}
}
