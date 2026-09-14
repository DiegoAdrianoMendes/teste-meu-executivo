import * as React from 'react';
import { fullWidthClassName, zeroRightClassName } from 'react-remove-scroll-bar/constants';
import { useMergeRefs } from 'use-callback-ref';
import { RemoveScrollSideCar as ScrollEffects } from 'react-remove-scroll/dist/es2019/SideEffect.js';
import type {
  IRemoveScrollProps,
  RemoveScrollEffectCallbacks,
  RemoveScrollType,
} from 'react-remove-scroll/dist/es5/types';

const noOp = () => undefined;

const ScrollLock = React.forwardRef<HTMLElement, IRemoveScrollProps>((props, forwardedRef) => {
  const lockRef = React.useRef<HTMLElement>(null);
  const [callbacks, setCallbacks] = React.useState<RemoveScrollEffectCallbacks>({
    onScrollCapture: noOp,
    onWheelCapture: noOp,
    onTouchMoveCapture: noOp,
  });
  const {
    forwardProps,
    children,
    className,
    removeScrollBar = true,
    enabled = true,
    shards,
    noRelative,
    noIsolation,
    inert = false,
    allowPinchZoom,
    as: Container = 'div',
    gapMode,
    ...rest
  } = props;
  const mergedRef = useMergeRefs([lockRef, forwardedRef]);
  const containerProps = { ...rest, ...callbacks };

  return (
    <React.Fragment>
      {enabled && (
        <ScrollEffects
          removeScrollBar={removeScrollBar}
          shards={shards}
          noRelative={noRelative}
          noIsolation={noIsolation}
          inert={inert}
          setCallbacks={setCallbacks}
          allowPinchZoom={Boolean(allowPinchZoom)}
          lockRef={lockRef}
          gapMode={gapMode}
        />
      )}
      {forwardProps
        ? React.cloneElement(React.Children.only(children), { ...containerProps, ref: mergedRef })
        : React.createElement(Container, { ...containerProps, className, ref: mergedRef }, children)}
    </React.Fragment>
  );
});

export const RemoveScroll = Object.assign(ScrollLock, {
  classNames: {
    fullWidth: fullWidthClassName,
    zeroRight: zeroRightClassName,
  },
}) as RemoveScrollType;

export default RemoveScroll;
