package clock

import (
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	fileOffsetEnv = "FAKETIME_TIMESTAMP_FILE"
	pollInterval  = 50 * time.Millisecond
)

func fileOffsetClockPath() string {
	return strings.TrimSpace(os.Getenv(fileOffsetEnv))
}

// FileOffsetClock implements Clock with ClusterClock's "+N" timestamp file.
// Now() is host UTC plus the offset. TickAfter polls real time so a jump
// past the deadline fires without waiting the original duration.
type FileOffsetClock struct {
	path string

	mu     sync.Mutex
	offset time.Duration
	have   bool
}

// NewFileOffsetClock constructs a clock that reads offset seconds from path.
func NewFileOffsetClock(path string) Clock {
	return &FileOffsetClock{path: path}
}

func (c *FileOffsetClock) readOffset() time.Duration {
	data, err := os.ReadFile(c.path)
	c.mu.Lock()
	defer c.mu.Unlock()
	if err != nil {
		if c.have {
			return c.offset
		}
		return 0
	}
	text := strings.TrimSpace(string(data))
	if !strings.HasPrefix(text, "+") {
		if c.have {
			return c.offset
		}
		return 0
	}
	secs, err := strconv.ParseInt(text[1:], 10, 64)
	if err != nil || secs < 0 {
		if c.have {
			return c.offset
		}
		return 0
	}
	c.offset = time.Duration(secs) * time.Second
	c.have = true
	return c.offset
}

// Now returns host time plus the current file offset.
func (c *FileOffsetClock) Now() time.Time {
	return time.Now().Add(c.readOffset())
}

// TickAfter returns a channel that receives once business time reaches now+duration.
func (c *FileOffsetClock) TickAfter(duration time.Duration) <-chan time.Time {
	ch := make(chan time.Time, 1)
	deadline := c.Now().Add(duration)
	go func() {
		ticker := time.NewTicker(pollInterval)
		defer ticker.Stop()
		for range ticker.C {
			now := c.Now()
			if !now.Before(deadline) {
				ch <- now
				return
			}
		}
	}()
	return ch
}
