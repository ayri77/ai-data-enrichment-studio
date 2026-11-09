// Adapted from the shadcn/ui button (MIT): https://ui.shadcn.com/docs/components/button
import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

const buttonVariants = cva(
    "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400 disabled:pointer-events-none disabled:opacity-40 [&_svg]:size-4",
    {
        variants: {
            variant: {
                default:
                    "bg-emerald-400 text-zinc-950 hover:bg-emerald-300 shadow-sm",
                outline:
                    "border border-white/15 bg-white/3 text-zinc-200 hover:bg-white/8",
            },
            size: { default: "h-10 px-4", sm: "h-8 px-3 text-xs" },
        },
        defaultVariants: { variant: "default", size: "default" },
    },
);
export interface ButtonProps
    extends
        React.ButtonHTMLAttributes<HTMLButtonElement>,
        VariantProps<typeof buttonVariants> {
    asChild?: boolean;
}
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
    function Button(
        { className, variant, size, asChild = false, ...props },
        ref,
    ) {
        const Comp = asChild ? Slot : "button";
        return (
            <Comp
                className={cn(buttonVariants({ variant, size, className }))}
                ref={ref}
                {...props}
            />
        );
    },
);
