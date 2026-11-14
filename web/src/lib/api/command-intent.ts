import {
  ApiError,
  captureApiSession,
  type MutationOptions,
} from "@/lib/api/client"

type Pending = {
  key: string
  fingerprint: string
  assertSession: () => void
  uncertain: boolean
}

function serialize(input: unknown): string {
  return JSON.stringify({ input }, (_key, value) =>
    value && typeof value === "object" && !Array.isArray(value)
      ? Object.fromEntries(
          Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
        )
      : value
  )
}

/** One submitted action, retaining its key and exact input until acknowledged. */
export class CommandIntent<Input, Output> {
  private pending: Pending | null = null
  private inFlight: Promise<Output> | null = null
  private send: (input: Input, options: MutationOptions) => Promise<Output>

  constructor(
    send: (input: Input, options: MutationOptions) => Promise<Output>
  ) {
    this.send = send
  }

  get uncertain() {
    return this.pending?.uncertain ?? false
  }

  submit(input: Input): Promise<Output> {
    const fingerprint = serialize(input)
    if (this.pending && this.pending.fingerprint !== fingerprint) {
      return Promise.reject(
        new ApiError(
          409,
          "unconfirmed_action",
          "Confirm the submitted action before changing its details."
        )
      )
    }
    this.pending ??= {
      key: crypto.randomUUID(),
      fingerprint,
      assertSession: captureApiSession(),
      uncertain: false,
    }
    return this.execute()
  }

  retry(): Promise<Output> {
    if (!this.pending)
      return Promise.reject(new Error("There is no submitted action to retry"))
    return this.execute()
  }

  private execute(): Promise<Output> {
    const pending = this.pending!
    try {
      pending.assertSession()
    } catch (error) {
      return Promise.reject(error)
    }
    if (this.inFlight) return this.inFlight
    this.inFlight = Promise.resolve()
      .then(() => {
        pending.assertSession()
        return this.send(JSON.parse(pending.fingerprint).input as Input, {
          idempotencyKey: pending.key,
        })
      })
      .then((result) => {
        pending.assertSession()
        this.pending = null
        return result
      })
      .catch((error) => {
        const definitive =
          error instanceof ApiError &&
          error.status >= 400 &&
          error.status < 500 &&
          !["session_changed", "http_error"].includes(error.code)
        if (definitive && !pending.uncertain) this.pending = null
        else pending.uncertain = true
        throw error
      })
      .finally(() => {
        this.inFlight = null
      })
    return this.inFlight
  }
}
