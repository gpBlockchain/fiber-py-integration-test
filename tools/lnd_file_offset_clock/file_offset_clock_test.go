package clock

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestFileOffsetClockNowAndTickAfterJump(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "faketime.rc")
	if err := os.WriteFile(path, []byte("+0\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	clock := NewFileOffsetClock(path)
	before := time.Now()
	now := clock.Now()
	if now.Before(before.Add(-2*time.Second)) || now.After(before.Add(2*time.Second)) {
		t.Fatalf("offset 0 Now() = %v, host %v", now, before)
	}

	ch := clock.TickAfter(time.Hour)
	if err := os.WriteFile(path, []byte("+3600\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	select {
	case fired := <-ch:
		if fired.Before(before.Add(3590 * time.Second)) {
			t.Fatalf("TickAfter fired too early at %v", fired)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("TickAfter did not fire within 2s after a 3600s file jump")
	}
}

func TestNewDefaultClockHonorsEnv(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "faketime.rc")
	if err := os.WriteFile(path, []byte("+120\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("FAKETIME_TIMESTAMP_FILE", path)
	clock := NewDefaultClock()
	host := time.Now()
	got := clock.Now()
	if got.Before(host.Add(100*time.Second)) || got.After(host.Add(140*time.Second)) {
		t.Fatalf("expected +120s, host=%v got=%v", host, got)
	}
}
