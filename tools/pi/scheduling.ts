/** A routine-only deadline: the worker keeps polling for human and material jobs. */
export class RoutineCooldown {
  private eligibleAt = 0;

  completed(now: number): void {
    this.eligibleAt = now + 15000;
  }

  nextRequest(now: number): { defer_routine: boolean } {
    return { defer_routine: now < this.eligibleAt };
  }
}
