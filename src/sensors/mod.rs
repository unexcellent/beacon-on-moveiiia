//! MOVE-IIIa sensor drivers: the SC850SL RGB camera and the MI48Dx thermal
//! camera. Both are generic over `beacon`'s [`CameraInterface`] and carry no
//! platform code of their own.
//!
//! [`CameraInterface`]: beacon::camera::CameraInterface

mod mi48;
mod sc850sl;

pub use mi48::Mi48;
pub use sc850sl::Sc850sl;
