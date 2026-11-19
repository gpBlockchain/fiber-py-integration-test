package clock

import (
	"time"
)

// DefaultClock implements Clock interface by simply calling the appropriate
// time functions.
type DefaultClock struct{}

// NewDefaultClock constructs a new DefaultClock.
//
// When FAKETIME_TIMESTAMP_FILE is set, this returns a FileOffsetClock so a
// test binary can share ClusterClock's +N offset file. Official LND releases
// are not built with this behavior; do not push this patch upstream.
func NewDefaultClock() Clock {
	if path := fileOffsetClockPath(); path != "" {
		return NewFileOffsetClock(path)
	}
	return &DefaultClock{}
}

// Now simply returns time.Now().
func (DefaultClock) Now() time.Time {
	return time.Now()
}

// TickAfter simply wraps time.After().
func (DefaultClock) TickAfter(duration time.Duration) <-chan time.Time {
	return time.After(duration)
}
