package worker

import (
	"campus/runner/internal/client"
	"campus/runner/internal/generated"
	"context"
	"errors"
	"time"
)

func (r *Runner) requestLease(ctx context.Context, a client.Assignment, operation string) (generated.LeaseReceipt, error) {
	var receipt generated.LeaseReceipt
	if err := r.Client.DoOnce(ctx, "POST", "/attempts/"+a.AttemptID+"/"+operation, a.Fence.Proof(), &receipt, ""); err != nil {
		return receipt, err
	}
	_, timeErr := time.Parse(time.RFC3339Nano, receipt.LeaseExpiresAt)
	if receipt.AttemptID != a.AttemptID || receipt.LeaseRemainingMs <= 0 || timeErr != nil || receipt.State != generated.TaskStateLeased && receipt.State != generated.TaskStateRunning {
		return receipt, errors.New("invalid attempt lease receipt identity, state, or duration")
	}
	return receipt, nil
}

func (r *Runner) reportAttemptError(ctx context.Context, a client.Assignment, release bool) error {
	if release {
		return r.Client.DoOnce(ctx, "POST", "/attempts/"+a.AttemptID+"/release", a.Fence.Proof(), nil, "")
	}
	retryable := false
	body := generated.Failure{Generation: a.Generation, LeaseToken: a.LeaseToken, Code: "EXECUTION_FAILED", Retryable: &retryable}
	return r.Client.DoOnce(ctx, "POST", "/attempts/"+a.AttemptID+"/fail", body, nil, "")
}
