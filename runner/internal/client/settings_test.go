package client

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestManagedSettingsUseDeviceCredentialWithoutSessionAndExplicitProtocol(t *testing.T) {
	var gets, acks int
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer device" || r.Header.Get("X-Node-Session") != "" || r.Header.Get("X-Campus-Worker-Protocol") != "2" {
			t.Errorf("wrong settings authentication/marker: %v", r.Header)
		}
		if r.URL.Path == "/api/v1/device/settings" {
			gets++
			fmt.Fprint(w, `{"revision":3,"mode":"low","desired_state":"paused"}`)
			return
		}
		if r.URL.Path == "/api/v1/device/settings/ack" {
			acks++
			w.WriteHeader(204)
			return
		}
		t.Errorf("unexpected route %s", r.URL.Path)
	}))
	defer s.Close()
	c, _ := New(s.URL, "device", "live-session", s.Client())
	c.Managed = true
	settings, e := c.DeviceSettings(context.Background())
	if e != nil || settings.Revision != 3 || settings.Mode != "low" || settings.DesiredState != "paused" {
		t.Fatal(settings, e)
	}
	if e = c.AckSettings(context.Background(), SettingsAck{Revision: 3, Mode: "low", ActualState: "paused"}); e != nil {
		t.Fatal(e)
	}
	if gets != 1 || acks != 1 || c.SessionID != "live-session" {
		t.Fatal(gets, acks, c.SessionID)
	}
	req, e := c.Request(context.Background(), "POST", "/work/next", nil, "")
	if e != nil || req.Header.Get("X-Campus-Worker-Protocol") != "2" || req.Header.Get("User-Agent") != "campus/0.1.0" {
		t.Fatal(req, e)
	}
	c.Managed = false
	req, _ = c.Request(context.Background(), "POST", "/work/next", nil, "")
	if req.Header.Get("X-Campus-Worker-Protocol") != "" {
		t.Fatal("legacy opted into rich assignments")
	}
}

func TestSettingsRejectInvalidRevisionOrState(t *testing.T) {
	for _, body := range []string{`{"revision":0,"mode":"low","desired_state":"running"}`, `{"revision":1,"mode":"extreme","desired_state":"running"}`, `{"revision":1,"mode":"low","desired_state":"deleted"}`} {
		s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, body) }))
		c, _ := New(s.URL, "device", "", s.Client())
		if _, e := c.DeviceSettings(context.Background()); e == nil {
			t.Fatal("invalid settings accepted", body)
		}
		s.Close()
	}
}
