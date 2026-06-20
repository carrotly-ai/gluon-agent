import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

// Proves the React + jsdom + RTL + jest-dom matcher chain works end-to-end.
// Any component-level test in the suite relies on exactly this plumbing.
function Greeting({ name }: { name: string }) {
  return <p>Hello, {name}!</p>
}

describe('vitest + RTL harness', () => {
  it('renders a component into jsdom and queries it', () => {
    render(<Greeting name="Gluon" />)
    expect(screen.getByText('Hello, Gluon!')).toBeInTheDocument()
  })
})
