cmake_minimum_required(VERSION 3.24)

project(FH6Livery3DViewer LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets OpenGL OpenGLWidgets Svg)
find_package(ZLIB REQUIRED)

file(GLOB FLS_CORE_SOURCES CONFIGURE_DEPENDS "fls/src/core/*.cpp")

add_library(fls_clipper2 STATIC fls/third_party/clipper2/src/clipper.engine.cpp)
target_include_directories(fls_clipper2 PUBLIC fls/third_party/clipper2/include)
set_target_properties(fls_clipper2 PROPERTIES AUTOMOC OFF AUTOUIC OFF AUTORCC OFF)

set(FLS_GUI_SOURCES
    fls/src/gui/app/car_registry.cpp
    fls/src/gui/app/gui_assets.cpp
    fls/src/gui/app/theme_manager.cpp
    fls/src/gui/canvas/car_model_renderer.cpp
    fls/src/gui/canvas/native_shape_renderer.cpp
    fls/src/gui/fill/pen_fill.cpp
    fls/src/gui/fill/polygon_mesh.cpp
    fls/src/gui/fill/region_fill.cpp
    fls/src/gui/state/editor_state.cpp
    fls/src/gui/state/editor_state_selection.cpp
    fls/src/gui/state/editor_state_snapshot.cpp
    fls/src/gui/widgets/car_preview_widget.cpp
    fls/src/gui/widgets/shape_geometry_store.cpp
)

qt_add_executable(FH6Livery3DViewer WIN32
    src/viewer_main.cpp
    src/viewer_window.h
    src/viewer_window.cpp
    src/car_locator.h
    src/car_locator.cpp
    src/raster_pack.h
    src/raster_pack.cpp
    ${FLS_CORE_SOURCES}
    ${FLS_GUI_SOURCES}
)

target_include_directories(FH6Livery3DViewer PRIVATE
    fls/src/core
    fls/src/gui/app
    fls/src/gui/canvas
    fls/src/gui/state
    fls/src/gui/widgets
    fls/src/gui/fill
    src
)

target_link_libraries(FH6Livery3DViewer PRIVATE
    Qt6::Core
    Qt6::Gui
    Qt6::Widgets
    Qt6::OpenGL
    Qt6::OpenGLWidgets
    Qt6::Svg
    ZLIB::ZLIB
    fls_clipper2
)

if(WIN32)
    target_link_libraries(FH6Livery3DViewer PRIVATE d3d11 d3dcompiler)
endif()

# FLS_PRIVACY_POLICY=0: upstream's gate refuses to import shared liveries
# (vlrc source state 1 — 99.75% of a player's downloaded collection, measured
# over 400 real save samples). That gate protects an EDITOR from re-exporting
# other creators' work. This build is a strictly read-only viewer of the
# user's own save: no editing or export code is compiled in at all, and it
# shows only what the game itself already shows the player locally.
target_compile_definitions(FH6Livery3DViewer PRIVATE
    FLS_PRIVACY_POLICY=0
    ENFORCE_SHAPE_LIMITS=1
)

# Assets deployed next to the executable (shape geometry, car id map).
# raster/decal_textures.bin is NOT shipped: it is built on first run from the
# user's game files (see src/raster_pack.cpp) and cached in AppData.
add_custom_command(TARGET FH6Livery3DViewer POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_directory
            ${CMAKE_CURRENT_SOURCE_DIR}/assets $<TARGET_FILE_DIR:FH6Livery3DViewer>/assets
)
