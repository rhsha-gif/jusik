import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-full text-sm font-medium cursor-pointer transition-[transform,box-shadow,background-color,opacity,border-color] duration-150 ease-out active:scale-[0.97] disabled:pointer-events-none disabled:opacity-45 disabled:active:scale-100 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default:
          "bg-[linear-gradient(180deg,color-mix(in_oklab,var(--qp-accent)_92%,white)_0%,var(--qp-accent)_100%)] text-white shadow-[0_1px_0_rgba(255,255,255,0.25)_inset,0_6px_16px_-4px_var(--qp-accent-soft),0_2px_6px_rgba(10,132,255,0.25)] hover:shadow-[0_1px_0_rgba(255,255,255,0.25)_inset,0_10px_22px_-6px_var(--qp-accent-soft),0_3px_8px_rgba(10,132,255,0.3)] hover:brightness-[1.04]",
        secondary:
          "bg-surface-solid text-ink border border-hairline shadow-sm hover:border-hairline-strong hover:bg-accent-soft",
        ghost: "text-ink hover:bg-accent-soft",
        outline: "border border-hairline text-ink hover:bg-accent-soft hover:border-hairline-strong",
        danger: "bg-danger-soft text-danger border border-danger/30 hover:bg-danger/15",
      },
      size: {
        default: "h-10 px-5",
        sm: "h-8 px-3.5 text-[13px]",
        lg: "h-12 px-7 text-[15px]",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, type, ...props }, ref) => (
    <button
      ref={ref}
      type={type ?? "button"}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  ),
);
Button.displayName = "Button";
