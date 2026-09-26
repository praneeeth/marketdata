import { ErrorState, LoadingState } from '@/components/common/states'
import { Component, type ErrorInfo, type ReactNode } from 'react'

interface RouteErrorBoundaryProps {
  children: ReactNode
}

interface RouteErrorBoundaryState {
  error: Error | null
}

export function RouteLoadingFallback() {
  return (
    <div className="flex min-h-[320px] items-center justify-center">
      <LoadingState label="Loading the page…" />
    </div>
  )
}

export class RouteErrorBoundary extends Component<RouteErrorBoundaryProps, RouteErrorBoundaryState> {
  state: RouteErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): RouteErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Page module failed to load:', error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <div className="card flex min-h-[320px] items-center justify-center">
          <ErrorState
            title="The page failed to load"
            message="Please retry; if it keeps happening, your browser may have cached an old version of the page."
            onRetry={() => window.location.reload()}
          />
        </div>
      )
    }

    return this.props.children
  }
}
