use clap::Parser;
use st_tray::{run, TrayArgs};

fn main() -> anyhow::Result<()> {
    run(TrayArgs::parse())
}
