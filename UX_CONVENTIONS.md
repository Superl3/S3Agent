# Global UX Conventions

## Stable Container Rule

- Windows, panels, and popups must keep a fixed size during user interaction.
- Interactions such as tab switching, toggles, mode changes, and test actions must not resize the container.
- Overflow content must be handled through internal scrolling (`overflow-y: auto`) rather than dynamic container growth.
- Plan fixed anchors first for each window: title/header area, footer/action row, and tab strip if present.
- In settings panels, keep top context fixed at top and action buttons fixed at bottom; scroll only the content section.
- Only explicit user-requested exceptions may bypass this rule.
