import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { TraceTimeline } from '@/components/assistant/TraceTimeline'

describe('TraceTimeline', () => {
  it('renders factual runtime events without reasoning content', () => {
    render(
      <TraceTimeline
        events={[
          { event: 'run_started', data: { task_id: 7 } },
          { event: 'context_prepared', data: { compressed: true } },
          { event: 'tool_call_start', data: { name: 'get_portfolio', arguments: { market: 'IN' } } },
          { event: 'tool_result', data: { name: 'get_portfolio', ok: true, preview: 'Holdings looked up' } },
          { event: 'model_usage', data: { input_tokens: 120, output_tokens: 30 } },
          { event: 'done', data: {} },
        ]}
      />,
    )

    expect(screen.getByTestId('assistant-trace')).toBeTruthy()
    expect(screen.getByText(/Done/)).toBeTruthy()
    expect(screen.queryByText('Context compressed and prepared')).toBeNull()
    expect(screen.queryByText('Calling tool: get_portfolio')).toBeNull()
    expect(screen.queryByText(/chain of thought|thinking process/i)).toBeNull()
  })

  it('shows provider token usage as a factual runtime event', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          { event: 'model_usage', data: { input_tokens: 120, output_tokens: 30 } },
          { event: 'done', data: {} },
        ]}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Run log/ }))
    expect(screen.getByText('Model usage: input 120, output 30')).toBeTruthy()
  })

  it('expands the factual steps from the compact summary', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          { event: 'tool_call_start', data: { name: 'get_portfolio', arguments: { market: 'IN' } } },
          { event: 'tool_result', data: { name: 'get_portfolio', ok: true, preview: 'Holdings looked up' } },
          {
            event: 'extension_event',
            data: {
              extension: 'tool_research',
              event: 'completed',
              data: { selected_tools: ['get_portfolio'] },
            },
          },
          { event: 'done', data: {} },
        ]}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Run log/ }))

    expect(screen.getByText('Calling tool: get_portfolio')).toBeTruthy()
    expect(screen.getByText('{"market":"IN"}')).toBeTruthy()
    expect(screen.getByText('Holdings looked up')).toBeTruthy()
    expect(screen.getByText('Tool research done: 1 chosen')).toBeTruthy()
  })

  it('distinguishes tool exposure and model-side search from execution', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          {
            event: 'extension_event',
            data: {
              extension: 'tool_research',
              event: 'exposure',
              data: { direct_tools: ['get_quote'], loaded_tools: [] },
            },
          },
          {
            event: 'extension_event',
            data: {
              extension: 'tool_research',
              event: 'searched',
              data: { selected_tools: ['get_fundamentals'] },
            },
          },
          { event: 'tool_call_start', data: { name: 'get_fundamentals', arguments: {} } },
          { event: 'done', data: {} },
        ]}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Run log/ }))

    expect(screen.getByText('Tool catalogue ready: 1 direct, 0 loaded')).toBeTruthy()
    expect(screen.getByText('Tool search done: 1 loaded')).toBeTruthy()
    expect(screen.getByText('Calling tool: get_fundamentals')).toBeTruthy()
  })
})
