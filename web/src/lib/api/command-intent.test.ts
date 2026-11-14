import { beforeEach, expect, it, vi } from "vitest"

import { ApiError, setTokenProvider } from "@/lib/api/client"
import { CommandIntent } from "@/lib/api/command-intent"

let generation = 0
beforeEach(() => {
  generation = 0
  setTokenProvider({
    get: () => "token",
    session: () => generation,
    clear: () => {},
    refresh: async () => false,
  })
})

it("retains the original input/key after an uncertain response and gives an acknowledged next action a new key", async () => {
  const send = vi
    .fn()
    .mockRejectedValueOnce(new TypeError("Lost response"))
    .mockResolvedValue({ id: "payment" })
  const command = new CommandIntent<
    { amount: string; allocations: { amount: string }[] },
    { id: string }
  >(send)
  const body = { amount: "12.34", allocations: [{ amount: "4.56" }] }
  await expect(command.submit(body)).rejects.toThrow("Lost response")
  body.amount = "99.00"
  body.allocations[0].amount = "99.00"
  await expect(command.submit(body)).rejects.toMatchObject({
    code: "unconfirmed_action",
  })
  await command.retry()
  expect(send.mock.calls[1][0]).toEqual({
    amount: "12.34",
    allocations: [{ amount: "4.56" }],
  })
  expect(send.mock.calls[1][1].idempotencyKey).toBe(
    send.mock.calls[0][1].idempotencyKey
  )
  await command.submit({ amount: "12.34", allocations: [{ amount: "4.56" }] })
  expect(send.mock.calls[2][1].idempotencyKey).not.toBe(
    send.mock.calls[0][1].idempotencyKey
  )
})

it("allows correcting a rejected first submission but retains an already uncertain action after a later rejection", async () => {
  const send = vi
    .fn()
    .mockRejectedValueOnce(
      new ApiError(422, "validation_error", "Invalid amount")
    )
    .mockRejectedValueOnce(new TypeError("Lost response"))
    .mockRejectedValueOnce(new ApiError(403, "forbidden", "Permission changed"))
    .mockResolvedValue({})
  const command = new CommandIntent<{ amount: string }, object>(send)
  await expect(command.submit({ amount: "0" })).rejects.toMatchObject({
    status: 422,
  })
  expect(command.uncertain).toBe(false)
  await expect(command.submit({ amount: "10" })).rejects.toThrow(
    "Lost response"
  )
  await expect(command.retry()).rejects.toMatchObject({ status: 403 })
  expect(command.uncertain).toBe(true)
  await command.retry()
  const keys = send.mock.calls.map((call) => call[1].idempotencyKey)
  expect(keys[0]).not.toBe(keys[1])
  expect(new Set(keys.slice(1)).size).toBe(1)
})

it("shares a concurrently submitted action and supports commands without a body", async () => {
  let finish!: (value: string) => void
  const send = vi.fn(
    () =>
      new Promise<string>((resolve) => {
        finish = resolve
      })
  )
  const command = new CommandIntent<undefined, string>(send)
  const first = command.submit(undefined)
  const second = command.submit(undefined)
  expect(first).toBe(second)
  await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(1))
  finish("posted")
  expect(await first).toBe("posted")
  expect(send).toHaveBeenCalledWith(undefined, {
    idempotencyKey: expect.any(String),
  })
})

it("does not send a queued action or retry an uncertain action across an account switch", async () => {
  const send = vi.fn().mockRejectedValue(new TypeError("Lost response"))
  const command = new CommandIntent<{ amount: string }, object>(send)
  const queued = command.submit({ amount: "1" })
  generation += 1
  await expect(queued).rejects.toMatchObject({ code: "session_changed" })
  expect(send).not.toHaveBeenCalled()
  const current = new CommandIntent<{ amount: string }, object>(send)
  await expect(current.submit({ amount: "2" })).rejects.toThrow("Lost response")
  generation += 1
  await expect(current.retry()).rejects.toMatchObject({
    code: "session_changed",
  })
  expect(send).toHaveBeenCalledTimes(1)
})
