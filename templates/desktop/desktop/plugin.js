// Desktop half of the forged plugin "{{name}}". Plain ESM, no JSX, no build step.
// Only these imports resolve: @hermes/plugin-sdk, react, react/jsx-runtime.
// The id must equal the plugin folder name.
import { host } from '@hermes/plugin-sdk'
import { jsx } from 'react/jsx-runtime'

export default {
  id: '{{name}}',
  name: '{{name}}',
  register(ctx) {
    // A status-bar item with a line of text. Replace the text, or swap `area` for 'panes' to get a pane.
    ctx.register({
      id: 'status',
      area: 'statusBar.right',
      order: 140,
      render: () =>
        jsx('button', {
          type: 'button',
          className: 'px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
          'aria-label': '{{name}} status',
          onClick: () => host.notify({ kind: 'info', message: 'Hello from {{name}}' }),
          children: '{{name}}'
        })
    })
  }
}
