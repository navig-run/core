package profilemgr_test

import (
	"navig-core/host/internal/browseragent/profilemgr"
	"testing"
)

func TestProfileRecordValidation(t *testing.T) {
	tests := []struct {
		name    string
		record  profilemgr.ProfileRecord
		wantErr bool
	}{
		{
			name: "valid record",
			record: profilemgr.ProfileRecord{
				ID:               "test-profile",
				PreferredEngine:  "chromedp",
				PreferredBrowser: "chrome",
			},
			wantErr: false,
		},
		{
			name: "empty id",
			record: profilemgr.ProfileRecord{
				ID: "",
			},
			wantErr: true,
		},
		{
			name: "invalid id chars",
			record: profilemgr.ProfileRecord{
				ID: "invalid id!",
			},
			wantErr: true,
		},
		{
			name: "invalid engine",
			record: profilemgr.ProfileRecord{
				ID:              "test",
				PreferredEngine: "invalid",
			},
			wantErr: true,
		},
		{
			name: "invalid browser",
			record: profilemgr.ProfileRecord{
				ID:               "test",
				PreferredBrowser: "invalid",
			},
			wantErr: true,
		},
		{
			name: "auto defaults",
			record: profilemgr.ProfileRecord{
				ID:               "test",
				PreferredEngine:  "auto",
				PreferredBrowser: "auto",
			},
			wantErr: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := tt.record.Validate()
			if (err != nil) != tt.wantErr {
				t.Errorf("Validate() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}
