package telemetry

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"os/exec"
	"runtime"
	"testing"
	"time"
)

func TestNativeProcessStatsReadActualChildPID(t *testing.T) {
	if runtime.GOOS == "darwin" {
		t.Skip("external macOS PID CPU/RSS measurement is pending native implementation")
	}
	command := exec.Command(os.Args[0], "-test.run=^TestNativeProcessStatsChildHelper$")
	command.Env = append(os.Environ(), "CAMPUS_PROCESS_STATS_CHILD=1")
	out, err := command.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	input, err := command.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err = command.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { command.Process.Kill(); command.Wait() }()
	reader := bufio.NewReader(out)
	if _, err = reader.ReadString('\n'); err != nil {
		t.Fatal(err)
	}
	before, err := ReadProcessStats(command.Process.Pid)
	if err != nil || before.CPUTimeNS == nil || before.RSSBytes == nil || *before.RSSBytes < 8<<20 {
		t.Fatalf("missing native child counters: %+v %v", before, err)
	}
	if _, err = io.WriteString(input, "run\n"); err != nil {
		t.Fatal(err)
	}
	if _, err = reader.ReadString('\n'); err != nil {
		t.Fatal(err)
	}
	after, err := ReadProcessStats(command.Process.Pid)
	if err != nil || after.CPUTimeNS == nil || *after.CPUTimeNS-*before.CPUTimeNS < int64(20*time.Millisecond) {
		t.Fatalf("child CPU work not measured: before %+v after %+v err %v", before, after, err)
	}
	if _, err = ReadProcessStats(-1); err == nil {
		t.Fatal("invalid PID manufactured a measurement")
	}
}

func TestNativeProcessStatsChildHelper(t *testing.T) {
	if os.Getenv("CAMPUS_PROCESS_STATS_CHILD") != "1" {
		return
	}
	memory := make([]byte, 12<<20)
	for i := range memory {
		memory[i] = byte(i)
	}
	fmt.Println("ready")
	reader := bufio.NewReader(os.Stdin)
	reader.ReadString('\n')
	until := time.Now().Add(150 * time.Millisecond)
	var result uint64
	for time.Now().Before(until) {
		for i := range 10000 {
			result += uint64(i) * uint64(i)
		}
	}
	fmt.Println(result)
	reader.ReadString('\n')
	runtime.KeepAlive(memory)
}
