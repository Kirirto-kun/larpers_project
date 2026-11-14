package client

import (
	"context"
	"errors"
)

type DeviceSettings struct {
	Revision     int64  `json:"revision"`
	Mode         string `json:"mode"`
	DesiredState string `json:"desired_state"`
}
type SettingsAck struct {
	Revision    int64  `json:"revision"`
	Mode        string `json:"mode"`
	ActualState string `json:"actual_state"`
	Error       string `json:"error,omitempty"`
}

func (s DeviceSettings) Validate() error {
	if s.Revision < 1 || (s.Mode != "low" && s.Mode != "medium" && s.Mode != "max") || (s.DesiredState != "running" && s.DesiredState != "paused" && s.DesiredState != "draining") {
		return errors.New("invalid device settings")
	}
	return nil
}
func (c *Client) DeviceSettings(ctx context.Context) (DeviceSettings, error) {
	device := *c
	device.SessionID = ""
	var s DeviceSettings
	if e := device.Do(ctx, "GET", "/device/settings", nil, &s, ""); e != nil {
		return s, e
	}
	return s, s.Validate()
}
func (c *Client) AckSettings(ctx context.Context, ack SettingsAck) error {
	if e := (DeviceSettings{Revision: ack.Revision, Mode: ack.Mode, DesiredState: ack.ActualState}).Validate(); e != nil {
		return e
	}
	device := *c
	device.SessionID = ""
	return device.DoOnce(ctx, "POST", "/device/settings/ack", ack, nil, "")
}
