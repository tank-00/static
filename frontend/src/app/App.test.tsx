import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { App } from './App'

describe('App shell', () => {
  it('renders the product title', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Utility Tariff Interpreter' })).toBeInTheDocument()
  })
})
